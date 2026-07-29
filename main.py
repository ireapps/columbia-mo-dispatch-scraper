"""
A project to scrape and archive police and fire dispatch calls in Columbia, MO. New incidents are flagged to a Slack channel. Data is stored locally as CSVs (fine for now, probably want a DB eventually).

- For each type of call (police and fire), Playwright loads the page and triggers the CSV download of the latest data
- New data is added to existing data and written back out to CSV
- New incidents are formatted and sent as webhook messages to a Slack channel
- The script runs every 30 minutes using a GitHub Action to check for new incidents
"""

import os
from pathlib import Path
from datetime import date, timedelta
import urllib.parse
import time

# https://pandas.pydata.org/
import pandas as pd

# https://playwright.dev/python
from playwright.sync_api import sync_playwright

# https://pyproj4.github.io/pyproj/stable/
from pyproj import Transformer

# https://docs.slack.dev/tools/python-slack-sdk
from slack_sdk.webhook import WebhookClient
from slack_sdk.errors import SlackApiError


# mo central state plane feet - will use this to convert coordinates to lat/lng
MO_CRS = "ESRI:102697"

# the URL we'll send slack alerts to
SLACK_WEBHOOK = os.getenv("SLACK_HOOK_PYTHON26", "")

# the slack API only allows sending 50 block elements at a time
MAX_SLACK_BLOCKS = 50

# we'll fill in start/end form inputs with today and yesterday
TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def transform_proj_to_wgs84(geo_x, geo_y, input_crs=MO_CRS):
    """Transform x/y coordinates from `input_crs` to WGS84 lat/lng"""

    transformer = Transformer.from_crs(input_crs, "EPSG:4326", always_xy=True)
    lon, lat = transformer.transform(geo_x, geo_y)

    return lon, lat


def send_slack_alert(call_type="police", df=None):
    """Format dispatch call data into Slack messages and send"""

    if not isinstance(df, pd.DataFrame):
        raise Exception("`df` should be a pd.DataFrame")

    if not len(df):
        print("`df` is empty, skipping slack alert ...")
        return

    def build_slack_block(row):
        """Given a row of data about an incident, format a Slack message block"""

        # strip whitespace from address
        address = " ".join(row["Address"].split())

        # set default google maps query - the address
        gmaps_query = urllib.parse.quote_plus(address) + "+Columbia+MO"

        # but if there are coordinates ...
        if row.get("geox") and row.get("geoy"):

            # get the lat/lng from the coordinates
            lon, lat = transform_proj_to_wgs84(row["geox"], row["geoy"])

            # ... and set them as the maps query
            gmaps_query = f"{round(lat, 4)},{round(lon, 4)}"

        # format the message for a police call
        if call_type == "police":
            txt = f">:cop: Police call - *{row['ExtNatureDisplayName']}* - {row["CallDateTime"]}\n>{address} (Dist. {row['PolArea'].strip()}) - <https://maps.google.com/?q={gmaps_query}|Google Maps>\n>_Incident No. {row['InNum']}_"

        # format the message for a fire call
        elif call_type == "fire":
            txt = f">:firefighter: Fire call - *{row['ExtNatureDisplayName']}* - {row["CallDateTime"]} - {row['Agency'].strip()}\n>{address} - <https://maps.google.com/?q={gmaps_query}|Google Maps>\n>_Incident No. {row['InNum']}_"

        return {
            "type": "section", 
            "text": {
                "type": "mrkdwn", 
                "text": txt
            }
        }

    # get the list of formatted block objects
    blocks = df.apply(build_slack_block, axis=1).tolist()

    # guard against empty blocks for whatever reason
    if not blocks:
        return

    # set up the webhook client
    webhook = WebhookClient(SLACK_WEBHOOK)

    # break the list into 50-block increments
    block_groups = [blocks[i:i + MAX_SLACK_BLOCKS] for i in range(0, len(blocks), MAX_SLACK_BLOCKS)]

    for bg in block_groups:
        # send the slack alert for this batch
        try:
            response = webhook.send(
                text="Could not display incident details ...",
                blocks=bg
            )

            time.sleep(1)

            print(f"    {response.status_code} - Sent Slack alert for {len(bg)} incident(s)")

        except SlackApiError as e:
            print(f"Error returned from Slack API: {e.response['error']} (Status code: {e.response.status_code})")

        time.sleep(1)

    return blocks


def scrape_data(call_type="police", page=None):
    """Main function to load the page with the correct type of dispatch calls,
        trigger the CSV download, clean/combine the data and 
        conditionally send a Slack alert.
    """

    if not page:
        raise Exception("Must provide a Playwright `page`")

    if not call_type or call_type.lower().strip() not in ["police", "fire"]:
        raise Exception("Must provide a `call_type` value: `police` or `fire`")

    call_type = call_type.lower().strip()

    print(f"Scraping {call_type} calls ...")

    # path to local file for this type of call
    filepath = Path(f"columbia-{call_type}-calls.csv")

    # set up some defaults
    df = pd.DataFrame()
    existing_ids = []

    # if the file already exists, load in existing data
    if filepath.exists():
        df = pd.read_csv(filepath, dtype={"CallDateTime": str})

        print(f"    Loaded {len(df):,} existing {call_type} records ...")

        # grab a list of existing IDs to use later for filtering for new incidents
        existing_ids = df["InNum"]

    # navigate to the page
    url = f"https://www.como.gov/CMS/911dispatch/{call_type}.php"
    page.goto(url)

    # fill input for start date with yesterday's date
    page.locator("input#Start_Date").fill(YESTERDAY.isoformat())

    # fill input for end date with today's date
    page.locator("input#End_Date").fill(TODAY.isoformat())

    # wait a sec
    time.sleep(1)

    # click the "Filter" button
    page.locator("input#Submit").click()

    # wait a few seconds
    time.sleep(3)

    # trigger the download
    # https://playwright.dev/python/docs/downloads
    with page.expect_download() as download_info:
        page.get_by_text("Export CSV").click()

    download = download_info.value

    # load the tmp downloaded CSV file into a dataframe
    new_df = pd.read_csv(download.path(), dtype={"CallDateTime": str})

    # add this to the existing dataframe
    df = pd.concat([df, new_df])

    # drop duplicates based on incident number
    df.drop_duplicates(
        subset=["InNum"],
        keep="last"
    )

    # write combined data to file
    df.to_csv(filepath, index=False)

    # filter for new incidents
    new_incidents = new_df[~new_df["InNum"].isin(existing_ids)]

    if len(new_incidents):
        send_slack_alert(call_type=call_type, df=new_incidents)

    return new_incidents


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        for call_type in ["police", "fire"]:
            scrape_data(call_type=call_type, page=page)
            time.sleep(2)

        browser.close()
