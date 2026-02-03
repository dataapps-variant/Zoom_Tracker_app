"""
LOAD DATA FROM GCS TO BIGQUERY (BATCH)
======================================

WHAT THIS DOES:
1. Reads raw JSON files from GCS bucket
2. Loads them into BigQuery raw_events table
3. Batch loading is FREE (vs streaming which costs $$$)

WHEN TO USE:
- If streaming insert fails
- To reload historical data
- To process large batches

HOW TO RUN:
  python load_gcs_to_bigquery.py 2026-02-03
"""

from google.cloud import bigquery
from google.cloud import storage
import os
import sys
from datetime import datetime, date

# ==============================================================================
# CONFIGURATION
# ==============================================================================

GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'your-project-id')
GCS_BUCKET = os.environ.get('GCS_BUCKET', 'zoom-tracker-data')
GCS_RAW_PREFIX = os.environ.get('GCS_RAW_PREFIX', 'raw')
BQ_DATASET = os.environ.get('BQ_DATASET', 'zoom_tracker')
BQ_TABLE = os.environ.get('BQ_TABLE', 'raw_events')

# ==============================================================================
# MAIN FUNCTION
# ==============================================================================

def load_gcs_to_bigquery(target_date):
    """
    Load JSON files from GCS to BigQuery

    WHY BATCH LOAD:
    - FREE (streaming costs $0.01 per 200MB)
    - Faster for large volumes
    - Better for backfills

    HOW:
    - Uses BigQuery load job
    - Reads JSON files from GCS path
    - Auto-detects schema (or uses existing table schema)
    """
    print(f"Loading data for {target_date}")

    # GCS path pattern
    gcs_uri = f"gs://{GCS_BUCKET}/{GCS_RAW_PREFIX}/{target_date}/*.json"
    print(f"Source: {gcs_uri}")

    # BigQuery table
    table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"
    print(f"Destination: {table_id}")

    # Create BigQuery client
    client = bigquery.Client(project=GCP_PROJECT_ID)

    # Configure load job
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,  # Append to existing
        # Schema auto-detection or use existing table schema
        autodetect=False,
    )

    # Start load job
    print("Starting load job...")
    load_job = client.load_table_from_uri(
        gcs_uri,
        table_id,
        job_config=job_config
    )

    # Wait for completion
    load_job.result()

    # Get results
    destination_table = client.get_table(table_id)
    print(f"Loaded {load_job.output_rows} rows")
    print(f"Table now has {destination_table.num_rows} total rows")

    return load_job.output_rows

def list_available_dates():
    """List dates that have data in GCS"""
    client = storage.Client(project=GCP_PROJECT_ID)
    bucket = client.bucket(GCS_BUCKET)

    # List all prefixes under /raw/
    blobs = bucket.list_blobs(prefix=f"{GCS_RAW_PREFIX}/")

    dates = set()
    for blob in blobs:
        # Extract date from path: raw/2026-02-03/event.json
        parts = blob.name.split('/')
        if len(parts) >= 2:
            date_str = parts[1]
            if len(date_str) == 10 and date_str[4] == '-':
                dates.add(date_str)

    return sorted(dates)

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 60)
    print("LOAD GCS DATA TO BIGQUERY")
    print("=" * 60)

    if len(sys.argv) > 1:
        if sys.argv[1] == '--list':
            # List available dates
            print("\nAvailable dates in GCS:")
            dates = list_available_dates()
            for d in dates:
                print(f"  - {d}")
            return
        else:
            # Load specific date
            target_date = sys.argv[1]
    else:
        # Default to today
        target_date = date.today().strftime('%Y-%m-%d')

    print(f"\nDate: {target_date}")
    print()

    try:
        rows = load_gcs_to_bigquery(target_date)
        print()
        print("=" * 60)
        print(f"SUCCESS! Loaded {rows} rows")
        print("=" * 60)
    except Exception as e:
        print(f"ERROR: {e}")

if __name__ == '__main__':
    main()
