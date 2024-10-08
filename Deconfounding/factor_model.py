import torch
import torch.nn as nn
import numpy as np
from tqdm import tqdm
from sklearn.model_selection import ShuffleSplit
import matplotlib.pyplot as plt
import os
import argparse
import json
import scipy.io as sio
from torch.utils.data import Dataset, DataLoader
from utils.rnn_utils import AutoRegressiveLSTM, ComputeLoss, check_for_nan, check_data_range
import torch.nn.functional as F


class FactorModel(nn.Module):
    def __init__(self, params, hyperparams, device='cuda'):
        super(FactorModel, self).__init__()
        self.num_treatments = params['num_treatments']
        self.num_covariates = params['num_covariates']
        self.num_confounders = params['num_confounders']
        self.max_sequence_length = params['max_sequence_length']
        self.num_epochs = params['num_epochs']

        self.rnn_hidden_units = hyperparams['rnn_hidden_units']
        self.fc_hidden_units = hyperparams['fc_hidden_units']
        self.learning_rate = hyperparams['learning_rate']
        self.batch_size = hyperparams['batch_size']
        self.rnn_keep_prob = hyperparams['rnn_keep_prob']
        self.device = device

        self.trainable_init_input = nn.Parameter(torch.Tensor(1, 1, self.num_covariates + self.num_treatments))
        self.trainable_h0, self.trainable_c0, self.trainable_z0 = self.trainable_init_h()
        self.lstm = AutoRegressiveLSTM(input_size=self.num_covariates + self.num_treatments,
                                       hidden_size=self.rnn_hidden_units, num_confounder=self.num_confounders).to(
            device)

        self.decoders = nn.ModuleList([
            nn.Sequential(
                nn.Linear(self.num_covariates + self.num_confounders, self.fc_hidden_units),
                nn.LeakyReLU(),
                nn.Linear(self.fc_hidden_units, 1)  
            ).to(device) for _ in range(57)
        ])

    def trainable_init_h(self):
        h0 = torch.zeros(1, self.rnn_hidden_units)
        c0 = torch.zeros(1, self.rnn_hidden_units)
        z0 = torch.zeros(1, self.num_confounders)
        trainable_h0 = nn.Parameter(h0, requires_grad=True)
        trainable_c0 = nn.Parameter(c0, requires_grad=True)
        trainable_z0 = nn.Parameter(z0, requires_grad=True)
        return trainable_h0, trainable_c0, trainable_z0

    def get_parameters(self):
        all_parameters = [self.trainable_init_input, self.trainable_h0, self.trainable_c0, self.trainable_z0]
        objects_ = [self.lstm] + list(self.decoders)
        for object_ in objects_:
            all_parameters += list(object_.parameters())
        return all_parameters


    def forward(self, previous_covariates, previous_treatments, current_covariates):
            input_size = previous_covariates.size(0)
            previous_covariates_and_treatments = torch.cat([previous_covariates, previous_treatments], -1).permute(1, 0, 2)
            rnn_input = torch.cat(
                [self.trainable_init_input.repeat(1, input_size, 1).to(self.device), previous_covariates_and_treatments], 0)
            rnn_input = rnn_input.float()
            self.rnn_input = rnn_input
            rnn_output, _ = self.lstm(rnn_input, (self.trainable_h0.repeat(input_size, 1).to(self.device),
                                                  self.trainable_c0.repeat(input_size, 1).to(self.device),
                                                  self.trainable_z0.repeat(input_size, 1).to(self.device)))
            
            hidden_confounders = rnn_output.view(-1, self.num_confounders)
            p_hidden_confounders = hidden_confounders.clone()
            
            current_covariates = current_covariates.permute(1, 0, 2)
            covariates = current_covariates.reshape(-1, self.num_covariates)
            
            if hidden_confounders.size(0) > covariates.size(0):
                padding_size = hidden_confounders.size(0) - covariates.size(0)
                covariates = F.pad(covariates, (0, 0, 0, padding_size))
            elif hidden_confounders.size(0) < covariates.size(0):
                padding_size = covariates.size(0) - hidden_confounders.size(0)
                p_hidden_confounders = F.pad(hidden_confounders, (0, 0, 0, padding_size))
            
            self.multitask_input = torch.cat([p_hidden_confounders, covariates], dim=-1).float()
            
            self.treatment_prob_predictions = []
            for treatment in range(self.num_treatments):
                self.treatment_prob_predictions.append(self.decoders[treatment](self.multitask_input))
            self.treatment_prob_predictions = torch.cat(self.treatment_prob_predictions, axis=-1)      

            max_sequence_length = previous_covariates.size(1)
            
            total_elements = hidden_confounders.numel()
            target_shape_elements = max_sequence_length * (total_elements // (max_sequence_length * self.num_confounders)) * self.num_confounders
            #print(f"Total elements in hidden_confounders: {total_elements}")
            #print(f"Target shape elements: {target_shape_elements}")
        
            if total_elements % (max_sequence_length * self.num_confounders) != 0:
                excess_elements = total_elements % (max_sequence_length * self.num_confounders)
                hidden_confounders = hidden_confounders[:-excess_elements]
                #print(f"Removed {excess_elements} excess elements from hidden_confounders.")
            
            return self.treatment_prob_predictions, hidden_confounders.view(max_sequence_length, -1, self.num_confounders)

    def compute_accuracy(self, target, prediction):
        target = target.view(-1, 1).float()
        prediction = prediction.view(-1, 1)
        target = (target >= 0.5).float()
        prediction = (prediction >= 0.5).float()
        total = target.shape[0]
        correct = (prediction == target).sum()
        return correct.item() / total * 100
    
    def compute_r2(self, target, prediction):
        target = target.view(-1, 1).float()
        prediction = prediction.view(-1, 1).float()
        ss_res = ((target - prediction) ** 2).sum()
        ss_tot = ((target - target.mean()) ** 2).sum()
        r2 = 1 - ss_res / ss_tot
        return r2.item()

    def train_model(self, trainloader, valloader):
            optimizer = torch.optim.Adam(self.get_parameters(), lr=self.learning_rate)
            loss_fn = nn.MSELoss()

            train_r2_list = []
            val_r2_list = []

            for epoch in tqdm(range(self.num_epochs)):
                self.train()
                train_losses = 0
                val_losses = 0
                total_samples = len(trainloader.dataset)
                batch_size = trainloader.batch_size
                num_batches = len(trainloader)

                train_r2_epoch = 0

                for i, (previous_covariates, previous_treatments, covariates, treatments, outcomes) in enumerate(trainloader):
                    previous_covariates = torch.nan_to_num(previous_covariates, nan=0.0).float().to(self.device)
                    previous_treatments = torch.nan_to_num(previous_treatments, nan=0.0).float().to(self.device)
                    covariates = torch.nan_to_num(covariates, nan=0.0).float().to(self.device)
                    treatments = torch.nan_to_num(treatments, nan=0.0).float().to(self.device)
                    outcomes = torch.nan_to_num(outcomes, nan=0.0).float().to(self.device)

                    treatment_prob_prediction, confounders = self.forward(previous_covariates, previous_treatments, covariates)
                    treatments = treatments.permute(1, 0, 2)
                    treatment_prob_target = treatments.reshape(-1, self.num_treatments)

                    min_size = min(treatment_prob_target.size(0), treatment_prob_prediction.size(0))
                    treatment_prob_target = treatment_prob_target[:min_size]
                    treatment_prob_prediction = treatment_prob_prediction[:min_size]

                    train_loss = loss_fn(treatment_prob_target, treatment_prob_prediction)
                    train_losses += train_loss.item()

                    train_r2_epoch += self.compute_r2(treatment_prob_target, treatment_prob_prediction)

                    optimizer.zero_grad()
                    train_loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.parameters(), max_norm=1.0)
                    optimizer.step()

                train_losses = train_losses / (i + 1)
                train_r2_epoch = train_r2_epoch / (i + 1)
                train_r2_list.append(train_r2_epoch)

                self.eval()
                val_r2_epoch = 0
                with torch.no_grad():
                    for i, (previous_covariates, previous_treatments, covariates, treatments, outcomes) in enumerate(valloader):
                        previous_covariates = torch.nan_to_num(previous_covariates, nan=0.0).float().to(self.device)
                        previous_treatments = torch.nan_to_num(previous_treatments, nan=0.0).float().to(self.device)
                        covariates = torch.nan_to_num(covariates, nan=0.0).float().to(self.device)
                        treatments = torch.nan_to_num(treatments, nan=0.0).float().to(self.device)
                        outcomes = torch.nan_to_num(outcomes, nan=0.0).float().to(self.device)

                        treatment_prob_prediction, _ = self.forward(previous_covariates, previous_treatments, covariates)
                        treatments = treatments.permute(1, 0, 2)
                        treatment_prob_target = treatments.reshape(-1, self.num_treatments)

                        min_size = min(treatment_prob_target.size(0), treatment_prob_prediction.size(0))
                        treatment_prob_target = treatment_prob_target[:min_size]
                        treatment_prob_prediction = treatment_prob_prediction[:min_size]

                        val_loss = loss_fn(treatment_prob_target, treatment_prob_prediction)
                        val_losses += val_loss.item()
                        val_r2_epoch += self.compute_r2(treatment_prob_target, treatment_prob_prediction)

                val_losses = val_losses / (i + 1)
                val_r2_epoch = val_r2_epoch / (i + 1)
                val_r2_list.append(val_r2_epoch)

                print(f"epoch {epoch} ---- train_loss: {train_losses:.5f} val_loss: {val_losses:.5f} train_r2: {train_r2_epoch:.2f} val_r2: {val_r2_epoch:.2f}")
                
                if epoch % 5 == 0:
                    if not os.path.exists('./checkpoints'):
                        os.mkdir('./checkpoints')
                    torch.save(self.state_dict(), './checkpoints/TSD_torch.pth')

            # Optionally, save the R2 values to a file
            with open('r2_values.txt', 'w') as f:
                for epoch in range(self.num_epochs):
                    f.write(f"epoch {epoch} ---- train_r2: {train_r2_list[epoch]:.2f} val_r2: {val_r2_list[epoch]:.2f}\n")
        

    def compute_hidden_confounders(self, allloader):
        confounders = []
        self.eval()
        for i, (previous_covariates, previous_treatments, covariates, treatments, outcomes) in enumerate(allloader):
            previous_covariates, previous_treatments, covariates, treatments, outcomes = \
                previous_covariates.to(self.device), previous_treatments.to(self.device), \
                    covariates.to(self.device), treatments.to(self.device), outcomes.to(self.device)
            _, confounder = self.forward(previous_covariates, previous_treatments, covariates)
            confounder = confounder.permute(1, 0, 2)
            confounder = confounder.cpu().detach().numpy()
            confounders.append(confounder)
        return np.concatenate(confounders, axis=0)

