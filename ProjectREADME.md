# Singapore Job Market Benchmarking Dashboard

This project is created to do exploratory data analysis (EDA), cleaning and normalizing data to be build into a functional dashboard that provides insights for companies who wish to hire in Singapore.

Focus will be EDA and cleaning pipeline for [SGJobData.csv], with an interactive Streamlit dashboard for exploring the results.

## Prerequisites

- Conda (Anaconda or Miniconda)

## Setup

1. Clone the repository
   ```bash
   git clone <repo-url>
   cd <repo-name>
   ```

2. Create the environment from `environment.yml`
   ```bash
   conda env create -f environment.yml
   conda activate pds
   ```

3. Open the notebook in VS Code and select the kernel
   - Open `EDA_Clean_Merged_v2.ipynb` in VS Code
   - Click **Select Kernel** in the top-right corner of the notebook
   - Choose **Python Environments...**
   - Select **pds** from the list of conda environments


## Usage

### Step 1: Run the EDA & Cleaning Notebook
Open the notebook, select the **pds** kernel, and run all cells top to bottom.
This generates the two files needed by the dashboard:
- `SGJobData_clean.csv` 
- `SGJobData_categories.csv` 

### Step 2: Launch the Dashboard
With the `pds` environment still active, in VS Code terminal run the following:
```bash
streamlit run app.py
```
The dashboard opens at `http://localhost:8501`.

### Step 3: To stop Dashboard
- On Windows, use Ctrl + c 
- On Mac, use Ctrl + c

## Report
See [`Mod01_Assignment_Written_Report.md`](./report.md) for the full written analysis and findings.

## Notes
- The notebook must be run in full before launching the dashboard: `app.py` will 
  raise a `FileNotFoundError` if `SGJobData_clean.csv` and `SGJobData_categories.csv` doesn't exist yet.
- Rows with missing values in `<column>` are dropped during cleaning, not imputed 
  see the "Cleaning Approach" section of [`Mod01_Assignment_Written_Report.md`](./report.md) for the reasoning.
- Date columns are parsed assuming `DD/MM/YYYY` format: this matches the source 
  data.
- **Pandas version-dependent frequency alias**: both the cleaning notebook 
  (`posting_month` creation) and `app.py`'s `load_data()` fallback use 
  `.dt.to_period("M")` to derive the `posting_month` column. 
  - In pandas ≥ 2.2, 
  the `'M'` alias is deprecated in favor of `'ME'` (month-end) and will raise 
  a `FutureWarning`/error depending on version. This project pins 
  `pandas==1.5.3` in `environment.yml`, so `'M'` works as-is 
  - **If you upgrade pandas independently, update both occurrences to `'ME'` at Cell 36 and Cell40.**
- The dashboard caches loaded CSVs with `@st.cache_data`: if you re-run the 
  notebook and regenerate the CSVs, restart the Streamlit app to see updated data.
