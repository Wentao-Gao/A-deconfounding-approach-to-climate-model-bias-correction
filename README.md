# A Deconfounding Approach to Climate Model Bias Correction

This repository contains the code for our AAAI submission paper: "A Deconfounding Approach to Climate Model Bias Correction."

Our study area is South Australia.

<img src="figures/Study_area_NCEP_final.png" alt="Study Area" width="70%">

As described in the paper, our method is divided into two parts: 'Deconfounding' and 'Correction'.

<img src="figures/Process_final.png" alt="Process Overview" width="70%">

## Data Preparation

### Simulation Data

To generate simulation data for Deconfounding, follow these steps:

1. Run the following command to generate the simulated datasets:

    ```python
    python main_run_simulation.py
    ```

   This will produce the simulated datasets from two sources and combine them into a CSV file. After the training process, a `.pkl` file will be generated.

2. To process the results and convert them into a CSV file, run:

    ```python
    python result_process.py 
    ```

### Real-World Data

For real-world data, download the necessary datasets from the following sources:

- **IPSL Data Portal:** [IPSL CMIP6 Data](https://aims2.llnl.gov/search/cmip6)
- **NCEP-NCAR Reanalysis 1 Data Portal:** [NCEP-NCAR Reanalysis](https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html)

For both datasets, select all variables at the surface level and at 2 meters, and download the monthly data. The data will be in `.nc` (NetCDF) format. Convert these `.nc` files to CSV format, and extract the relevant data for South Australia.

**Note:** For IPSL data, you'll need to download all experimental settings, ranging from `r1p1i1f1` to `r33p1i1f1`.

After obtaining the South Australia CSV file, remove any columns with a significant amount of missing data.

## Deconfounding

The first part, Deconfounding, is crucial as it introduces insights from deconfounding in causal inference to climate bias correction. Unlike traditional methods, it does not assume that all variables are observed. We demonstrate our approach using a simulation dataset, which is created based on the summary causal graph shown below:

<img src="figures/Summary%20causal%20graph_final.png" alt="Summary Causal Graph" width="70%">

For the case study using real-world data, you can download the datasets from the links below:

- IPSL Data Portal: [IPSL CMIP6 Data](https://aims2.llnl.gov/search/cmip6)
- NCEP-NCAR Reanalysis 1 Data Portal: [NCEP-NCAR Reanalysis](https://psl.noaa.gov/data/gridded/data.ncep.reanalysis.html)

<img src="figures/factor%20model.png" alt="Factor Model" width="70%">

## Correction

The second part, Correction, involves using the latent confounder learned in the Deconfounding step as an additional feature for precipitation correction. As described in the paper, we chose the state-of-the-art model, iTransformer, to perform this step. The implementation can be found at [iTransformer GitHub Repository](https://github.com/thuml/iTransformer).
