# Columbia dispatch scraper

Downloading and archiving raw data on fire and police calls in Columbia, MO.

[This Python script](main.py):
1. Launches a Playwright browser that navigates to police and fire call dispatch pages
2. For each page, loads into a dataframe the data from an exported CSV containing the previous day's worth of calls
3. Sends a Slack alert with details for any new incidents, then adds these records to the data in the local files ([`columbia-fire-calls.csv`](columbia-fire-calls.csv) and [`columbia-police-calls.csv`](columbia-police-calls.csv))

A GitHub action runs this script every 30 minutes.

## Running the download script
I'm using `uv` to manage Python dependencies.

1. Clone or download this repo and `cd` into the directory
2. `uv sync`
3. `uv run main.py`

## Analyzing the data
Launch the notebook server: `uv run jupyter lab`. See [`Analysis.ipynb`](Analysis.ipynb) for a start.