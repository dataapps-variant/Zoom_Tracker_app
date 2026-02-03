"""
UPDATE CAMERA DATA IN BIGQUERY
==============================

WHAT THIS DOES:
1. Fetches QOS (Quality of Service) data from Zoom API
2. Extracts camera on/off time for each participant
3. Updates daily_reports table in BigQuery with camera data

WHY SEPARATE:
- Webhook only captures join/leave (no camera data)
- QOS data available ~2-3 hours after meeting ends
- Must run separately after meeting

FIELDS UPDATED:
- camera_on_mins: Minutes with camera ON
- camera_off_mins: Minutes with camera OFF
- camera_percentage: % of time camera was ON

HOW TO RUN:
  python update_camera_data.py 2026-02-03
"""

import requests
import urllib.parse
from datetime import datetime, date
from google.cloud import bigquery
import sys
import os

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Zoom API credentials
ACCOUNT_ID = os.environ.get('ZOOM_ACCOUNT_ID', 'xhKbAsmnSM6pNYYYurmqIA')
CLIENT_ID = os.environ.get('ZOOM_CLIENT_ID', '2ysNg6WLS0Sm8bKVVDeXcQ')
CLIENT_SECRET = os.environ.get('ZOOM_CLIENT_SECRET', 'iWgD4lZrbkxOWiGEjTgwAc3ZHSC6K5xZ')
MEETING_ID = os.environ.get('ZOOM_MEETING_ID', '9034027764')

# GCP Configuration
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'your-project-id')
BQ_DATASET = os.environ.get('BQ_DATASET', 'zoom_tracker')

# ==============================================================================
# ZOOM API FUNCTIONS
# ==============================================================================

def get_zoom_token():
    """
    Get OAuth token from Zoom API

    HOW IT WORKS:
    - Uses Server-to-Server OAuth (no user login needed)
    - Token valid for 1 hour
    - Credentials set in Zoom Marketplace app
    """
    print("  Getting Zoom API token...", end=" ")

    url = 'https://zoom.us/oauth/token'
    params = {'grant_type': 'account_credentials', 'account_id': ACCOUNT_ID}

    try:
        response = requests.post(url, params=params, auth=(CLIENT_ID, CLIENT_SECRET), timeout=30)

        if response.status_code == 200:
            token = response.json().get('access_token')
            print("OK")
            return token
        else:
            print(f"FAILED ({response.status_code})")
            return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None

def get_meeting_uuid(token, target_date):
    """
    Find meeting UUID for a specific date

    WHY NEEDED:
    - Each meeting instance has unique UUID
    - Same meeting ID can have multiple instances (recurring)
    - Need UUID to fetch QOS data for that specific instance
    """
    print("  Finding meeting UUID...", end=" ")

    headers = {'Authorization': f'Bearer {token}'}
    url = f'https://api.zoom.us/v2/past_meetings/{MEETING_ID}/instances'

    try:
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code != 200:
            print(f"FAILED ({response.status_code})")
            return None

        meetings = response.json().get('meetings', [])

        for m in meetings:
            start = m.get('start_time', '')
            if start:
                meeting_date = datetime.fromisoformat(start.replace('Z', '+00:00')).date()
                if meeting_date == target_date:
                    uuid = m.get('uuid')
                    print(f"Found: {uuid[:20]}...")
                    return uuid

        print("Not found for this date")
        return None

    except Exception as e:
        print(f"ERROR: {e}")
        return None

def fetch_qos_data(token, meeting_uuid):
    """
    Fetch QOS (Quality of Service) data from Zoom API

    WHAT QOS DATA CONTAINS:
    - Audio/video bitrate samples (every ~1 minute)
    - Latency, jitter, packet loss
    - Resolution, frame rate

    HOW WE DETECT CAMERA ON:
    - video_input.bitrate > 50 kbps = camera is ON
    - video_input.bitrate <= 50 kbps = camera is OFF
    - Each sample = ~1 minute of data
    """
    print("  Fetching QOS data...", end=" ")

    if not meeting_uuid:
        print("No UUID")
        return {}

    headers = {'Authorization': f'Bearer {token}'}
    encoded_uuid = urllib.parse.quote(meeting_uuid, safe='')
    url = f'https://api.zoom.us/v2/metrics/meetings/{encoded_uuid}/participants/qos'

    all_qos = []
    next_token = None
    page = 0

    while True:
        params = {'type': 'past', 'page_size': 100}
        if next_token:
            params['next_page_token'] = next_token

        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)

            if response.status_code != 200:
                break

            data = response.json()
            participants = data.get('participants', [])
            all_qos.extend(participants)

            page += 1
            next_token = data.get('next_page_token')

            if not next_token:
                break

        except Exception as e:
            print(f"ERROR: {e}")
            break

    print(f"{len(all_qos)} participants")

    # Process QOS data
    video_stats = {}

    for p in all_qos:
        name = p.get('user_name', 'Unknown')
        qos_list = p.get('user_qos', []) or p.get('qos', [])

        video_on = 0   # Samples with camera ON
        video_off = 0  # Samples with camera OFF

        for qos in qos_list:
            bitrate_str = qos.get('video_input', {}).get('bitrate', '0') or '0'
            try:
                # Bitrate format: "123 kbps" or just "123"
                bitrate = int(str(bitrate_str).split()[0]) if bitrate_str else 0
            except:
                bitrate = 0

            # > 50 kbps = camera sending video
            if bitrate > 50:
                video_on += 1
            else:
                video_off += 1

        total = video_on + video_off
        video_stats[name] = {
            'camera_on': video_on,
            'camera_off': video_off,
            'camera_pct': round(video_on / total * 100, 1) if total > 0 else 0,
            'total_samples': total
        }

    return video_stats

# ==============================================================================
# BIGQUERY FUNCTIONS
# ==============================================================================

def update_bigquery_camera_data(target_date, video_stats):
    """
    Update daily_reports table with camera data

    WHAT IT UPDATES:
    - camera_on_mins
    - camera_off_mins
    - camera_percentage

    HOW:
    - Matches by report_date and participant_name
    - Uses UPDATE statement
    """
    print("  Updating BigQuery...", end=" ")

    client = bigquery.Client(project=GCP_PROJECT_ID)
    table = f"{GCP_PROJECT_ID}.{BQ_DATASET}.daily_reports"

    updated = 0
    errors = 0

    for name, stats in video_stats.items():
        # Escape single quotes in names
        safe_name = name.replace("'", "\\'")

        query = f"""
        UPDATE `{table}`
        SET
            camera_on_mins = {stats['camera_on']},
            camera_off_mins = {stats['camera_off']},
            camera_percentage = {stats['camera_pct']}
        WHERE report_date = '{target_date}'
          AND participant_name = '{safe_name}'
        """

        try:
            job = client.query(query)
            job.result()  # Wait for completion
            if job.num_dml_affected_rows > 0:
                updated += job.num_dml_affected_rows
        except Exception as e:
            errors += 1
            print(f"\n    Error updating {name}: {e}")

    print(f"{updated} rows updated, {errors} errors")
    return updated

def show_report_preview(target_date):
    """Show preview of updated report"""
    client = bigquery.Client(project=GCP_PROJECT_ID)

    query = f"""
    SELECT
        participant_name,
        room_name,
        room_duration_mins,
        camera_on_mins,
        camera_off_mins,
        camera_percentage
    FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.daily_reports`
    WHERE report_date = '{target_date}'
    ORDER BY participant_name, room_number
    LIMIT 10
    """

    print("\nReport Preview:")
    print("-" * 80)
    print(f"{'Name':<25} {'Room':<10} {'Duration':<10} {'Cam ON':<10} {'Cam OFF':<10} {'Cam %':<10}")
    print("-" * 80)

    results = client.query(query).result()
    for row in results:
        print(f"{row.participant_name[:24]:<25} {row.room_name:<10} {row.room_duration_mins:<10} {row.camera_on_mins:<10} {row.camera_off_mins:<10} {row.camera_percentage:<10}")

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print("=" * 60)
    print("UPDATE CAMERA DATA IN BIGQUERY")
    print("=" * 60)

    # Get target date
    if len(sys.argv) > 1:
        target_date = datetime.strptime(sys.argv[1], '%Y-%m-%d').date()
    else:
        target_date = date.today()

    print(f"Date: {target_date}")
    print()

    # Step 1: Get Zoom token
    print("[1/4] Zoom API Authentication")
    token = get_zoom_token()
    if not token:
        print("FAILED: Could not get Zoom token")
        return

    # Step 2: Find meeting UUID
    print("\n[2/4] Find Meeting Instance")
    meeting_uuid = get_meeting_uuid(token, target_date)
    if not meeting_uuid:
        print("FAILED: Meeting not found for this date")
        return

    # Step 3: Fetch QOS data
    print("\n[3/4] Fetch Camera Data (QOS)")
    video_stats = fetch_qos_data(token, meeting_uuid)
    if not video_stats:
        print("WARNING: No QOS data found")

    # Step 4: Update BigQuery
    print("\n[4/4] Update BigQuery")
    updated = update_bigquery_camera_data(str(target_date), video_stats)

    # Show preview
    if updated > 0:
        show_report_preview(str(target_date))

    print()
    print("=" * 60)
    print("DONE!")
    print("=" * 60)

if __name__ == '__main__':
    main()
