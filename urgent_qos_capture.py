
"""
==========================================================================
URGENT QOS CAPTURE SCRIPT
==========================================================================
RUN THIS IMMEDIATELY AFTER YOUR MEETING ENDS (within 30 minutes!)

Usage: python urgent_qos_capture.py
"""

import requests
import urllib.parse
import csv
import json
import os
from datetime import datetime

# Credentials (set these as environment variables or replace with your values)
ACCOUNT_ID = os.environ.get('ZOOM_ACCOUNT_ID', 'YOUR_ACCOUNT_ID')
CLIENT_ID = os.environ.get('ZOOM_CLIENT_ID', 'YOUR_CLIENT_ID')
CLIENT_SECRET = os.environ.get('ZOOM_CLIENT_SECRET', 'YOUR_CLIENT_SECRET')
MEETING_ID = os.environ.get('ZOOM_MEETING_ID', 'YOUR_MEETING_ID')

def get_access_token():
    url = "https://zoom.us/oauth/token"
    params = {"grant_type": "account_credentials", "account_id": ACCOUNT_ID}
    response = requests.post(url, params=params, auth=(CLIENT_ID, CLIENT_SECRET))
    return response.json().get('access_token')

def find_latest_meeting(token):
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.zoom.us/v2/past_meetings/{MEETING_ID}/instances"
    response = requests.get(url, headers=headers)
    meetings = response.json().get('meetings', [])
    meetings.sort(key=lambda x: x.get('start_time', ''), reverse=True)
    return meetings[0].get('uuid') if meetings else None

def fetch_qos_data(token, meeting_uuid):
    headers = {"Authorization": f"Bearer {token}"}
    encoded_uuid = urllib.parse.quote(meeting_uuid, safe='')
    url = f"https://api.zoom.us/v2/metrics/meetings/{encoded_uuid}/participants/qos"
    
    all_participants = []
    next_token = None
    
    while True:
        params = {"type": "past", "page_size": 300}
        if next_token:
            params['next_page_token'] = next_token
        
        response = requests.get(url, headers=headers, params=params, timeout=60)
        if response.status_code != 200:
            break
        
        data = response.json()
        all_participants.extend(data.get('participants', []))
        next_token = data.get('next_page_token')
        if not next_token:
            break
    
    return all_participants

def generate_report(participants):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"QOS_CAMERA_REPORT_{timestamp}.csv"
    
    results = []
    for p in participants:
        qos = p.get('qos', [])
        video_on = sum(1 for q in qos if (q.get('video_input', {}).get('bitrate') or 0) > 50)
        video_off = len(qos) - video_on
        
        results.append({
            'Name': p.get('user_name', 'Unknown'),
            'Total Mins': len(qos),
            'Video ON': video_on,
            'Video OFF': video_off,
            'Video %': f"{(video_on/len(qos)*100):.1f}%" if qos else "N/A"
        })
    
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['Name', 'Total Mins', 'Video ON', 'Video OFF', 'Video %'])
        writer.writeheader()
        writer.writerows(results)
    
    print(f"Report saved: {output_file}")
    print(f"Participants: {len(results)}")

def main():
    print("Starting QOS Capture...")
    token = get_access_token()
    uuid = find_latest_meeting(token)
    participants = fetch_qos_data(token, uuid)
    generate_report(participants)

if __name__ == "__main__":
    main()
