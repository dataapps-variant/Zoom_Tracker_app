"""
DOWNLOAD AND GENERATE REPORT
============================
Downloads webhook data from cloud and generates daily report.

Usage:
    python download_and_report.py                    # Today's date
    python download_and_report.py 2026-02-02         # Specific date
    python download_and_report.py --webhook-url URL  # Custom webhook URL
"""

import requests
import json
import csv
import os
import sys
import urllib.parse
from datetime import datetime, date
from collections import defaultdict

# ==============================================================================
# CONFIGURATION
# ==============================================================================
# Cloud webhook URL (update this after deploying to Render)
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', 'https://your-app.onrender.com')

# Zoom API credentials
ACCOUNT_ID = 'xhKbAsmnSM6pNYYYurmqIA'
CLIENT_ID = '2ysNg6WLS0Sm8bKVVDeXcQ'
CLIENT_SECRET = 'iWgD4lZrbkxOWiGEjTgwAc3ZHSC6K5xZ'
MEETING_ID = '9034027764'

# ==============================================================================
# FUNCTIONS
# ==============================================================================

def download_webhook_data(webhook_url, date_filter=None):
    """Download data from cloud webhook"""
    print(f'[1/5] Downloading data from cloud webhook...')

    try:
        url = f'{webhook_url}/data'
        if date_filter:
            url = f'{webhook_url}/data/today'

        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            events = data.get('events', [])
            print(f'      Downloaded {len(events)} events')
            return events
        else:
            print(f'      Error: HTTP {response.status_code}')
            return []
    except Exception as e:
        print(f'      Error connecting to webhook: {e}')
        print(f'      Trying local file instead...')

        # Fall back to local file
        try:
            with open('zoom_raw_payloads.json', 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if content.startswith('['):
                    events = json.loads(content)
                else:
                    events = json.loads('[' + content.rstrip(',\n') + ']')
            print(f'      Loaded {len(events)} events from local file')
            return events
        except:
            print('      No local data available')
            return []

def get_zoom_token():
    """Get OAuth token from Zoom"""
    url = 'https://zoom.us/oauth/token'
    params = {'grant_type': 'account_credentials', 'account_id': ACCOUNT_ID}
    response = requests.post(url, params=params, auth=(CLIENT_ID, CLIENT_SECRET))
    return response.json().get('access_token')

def get_meeting_uuid(token, target_date):
    """Find meeting UUID for the target date"""
    headers = {'Authorization': f'Bearer {token}'}
    url = f'https://api.zoom.us/v2/past_meetings/{MEETING_ID}/instances'
    response = requests.get(url, headers=headers)
    meetings = response.json().get('meetings', [])

    for m in meetings:
        start = m.get('start_time', '')
        if start:
            meeting_date = datetime.fromisoformat(start.replace('Z', '+00:00')).date()
            if meeting_date == target_date:
                return m.get('uuid')
    return None

def fetch_qos_data(token, meeting_uuid):
    """Fetch camera/video QOS data from Zoom API"""
    if not meeting_uuid:
        return {}

    headers = {'Authorization': f'Bearer {token}'}
    encoded_uuid = urllib.parse.quote(meeting_uuid, safe='')
    url = f'https://api.zoom.us/v2/metrics/meetings/{encoded_uuid}/participants/qos'

    video_stats = {}
    all_qos = []
    next_token = None

    while True:
        params = {'type': 'past', 'page_size': 100}
        if next_token:
            params['next_page_token'] = next_token

        try:
            response = requests.get(url, headers=headers, params=params, timeout=60)
            if response.status_code != 200:
                break
            data = response.json()
            all_qos.extend(data.get('participants', []))
            next_token = data.get('next_page_token')
            if not next_token:
                break
        except Exception as e:
            print(f'      API error: {e}')
            break

    for p in all_qos:
        name = p.get('user_name', 'Unknown')
        qos_list = p.get('user_qos', []) or p.get('qos', [])
        video_on = 0
        video_off = 0

        for qos in qos_list:
            bitrate_str = qos.get('video_input', {}).get('bitrate', '0') or '0'
            try:
                bitrate = int(bitrate_str.split()[0]) if bitrate_str else 0
            except:
                bitrate = 0
            if bitrate > 50:
                video_on += 1
            else:
                video_off += 1

        total = video_on + video_off
        video_stats[name] = {
            'camera_on': video_on,
            'camera_off': video_off,
            'camera_pct': round(video_on / total * 100, 1) if total > 0 else 0
        }

    return video_stats

def process_webhook_data(events, target_date):
    """Process webhook events for the target date"""
    filtered = []
    for p in events:
        ts = p.get('event_ts', 0)
        if ts:
            event_date = datetime.fromtimestamp(ts/1000).date()
            if event_date == target_date:
                filtered.append(p)

    rooms = defaultdict(lambda: {'participants': set(), 'joins': 0})
    journeys = defaultdict(list)

    for p in filtered:
        event = p.get('event', '')
        obj = p.get('payload', {}).get('object', {})
        participant = obj.get('participant', {})

        room_uuid = obj.get('breakout_room_uuid', '')
        name = participant.get('user_name', '')
        email = participant.get('email', '')
        ts = p.get('event_ts', 0)

        if not room_uuid or not name:
            continue

        action = 'JOIN' if 'joined' in event else 'LEAVE'
        rooms[room_uuid]['participants'].add(name)
        if action == 'JOIN':
            rooms[room_uuid]['joins'] += 1

        journeys[name].append({'ts': ts, 'room': room_uuid, 'action': action, 'email': email})

    return rooms, journeys

def generate_report(journeys, video_stats, target_date):
    """Generate CSV report"""
    os.makedirs('reports', exist_ok=True)
    report_file = f'reports/DAILY_REPORT_{target_date}.csv'

    with open(report_file, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow([
            'Participant Name', 'Email ID', 'Meeting Join Time', 'Meeting Left Time',
            'Meeting Duration (mins)', 'Room Name', 'Room Join Time', 'Room Left Time',
            'Room Duration (mins)', 'Camera ON (mins)', 'Camera OFF (mins)', 'Camera %', 'Next Room'
        ])

        for name, events in sorted(journeys.items()):
            events.sort(key=lambda x: x['ts'])

            vstats = video_stats.get(name, {})
            total_cam_on = vstats.get('camera_on', 0)
            total_cam_off = vstats.get('camera_off', 0)

            email = ''
            meeting_join = None
            meeting_leave = None

            for e in events:
                if e.get('email'):
                    email = e['email']
                if meeting_join is None or e['ts'] < meeting_join:
                    meeting_join = e['ts']
                if meeting_leave is None or e['ts'] > meeting_leave:
                    meeting_leave = e['ts']

            meeting_join_str = datetime.fromtimestamp(meeting_join/1000).strftime('%H:%M:%S') if meeting_join else ''
            meeting_leave_str = datetime.fromtimestamp(meeting_leave/1000).strftime('%H:%M:%S') if meeting_leave else ''
            meeting_duration = round((meeting_leave - meeting_join) / 60000, 1) if meeting_join and meeting_leave else 0

            visits = []
            for i, e in enumerate(events):
                if e['action'] != 'JOIN':
                    continue
                room_uuid = e['room']
                join_ts = e['ts']
                leave_ts = None
                for j in range(i+1, len(events)):
                    if events[j]['room'] == room_uuid and events[j]['action'] == 'LEAVE':
                        leave_ts = events[j]['ts']
                        break
                visits.append({'room': room_uuid, 'join': join_ts, 'leave': leave_ts})

            total_room_time = sum((v['leave'] - v['join'])/60000 for v in visits if v['leave']) or 1

            for idx, visit in enumerate(visits):
                room_join = datetime.fromtimestamp(visit['join']/1000).strftime('%H:%M:%S')
                if visit['leave']:
                    room_leave = datetime.fromtimestamp(visit['leave']/1000).strftime('%H:%M:%S')
                    room_dur = round((visit['leave'] - visit['join']) / 60000, 1)
                else:
                    room_leave = ''
                    room_dur = 0

                if room_dur > 0:
                    room_cam_on = round(total_cam_on * (room_dur / total_room_time), 1)
                    room_cam_off = round(total_cam_off * (room_dur / total_room_time), 1)
                    room_cam_pct = round((room_cam_on / room_dur) * 100, 1) if room_dur > 0 else 0
                else:
                    room_cam_on = room_cam_off = room_cam_pct = 0

                next_room = 'Left Meeting'
                if idx + 1 < len(visits):
                    next_room = f'Room-{idx+2}'

                w.writerow([
                    name, email, meeting_join_str, meeting_leave_str, meeting_duration,
                    f'Room-{idx+1}', room_join, room_leave, room_dur,
                    room_cam_on, room_cam_off, f'{room_cam_pct}%', next_room
                ])

    return report_file

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    print('=' * 70)
    print('ZOOM BREAKOUT ROOM TRACKER - CLOUD EDITION')
    print('=' * 70)

    # Parse command line arguments
    webhook_url = WEBHOOK_URL
    date_str = date.today().strftime('%Y-%m-%d')

    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--webhook-url' and i+2 < len(sys.argv):
            webhook_url = sys.argv[i+2]
        elif arg.count('-') == 2:  # Date format YYYY-MM-DD
            date_str = arg

    target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    print(f'Date: {date_str}')
    print(f'Webhook: {webhook_url}')
    print()

    # Step 1: Download webhook data
    events = download_webhook_data(webhook_url)

    # Step 2: Process for target date
    print(f'[2/5] Processing data for {date_str}...', end=' ')
    rooms, journeys = process_webhook_data(events, target_date)
    print(f'{len(rooms)} rooms, {len(journeys)} participants')

    # Step 3: Get camera data from Zoom API
    print('[3/5] Fetching camera data from Zoom API...', end=' ')
    token = get_zoom_token()
    video_stats = {}
    if token:
        meeting_uuid = get_meeting_uuid(token, target_date)
        if meeting_uuid:
            video_stats = fetch_qos_data(token, meeting_uuid)
            print(f'{len(video_stats)} participants with QOS data')
        else:
            print('Meeting not found')
    else:
        print('Token failed')

    # Step 4: Generate report
    print('[4/5] Generating report...')
    if journeys:
        report_file = generate_report(journeys, video_stats, date_str)
        print(f'      [OK] {report_file}')
    else:
        print('      No data to generate report')
        return

    # Step 5: Show preview
    print('[5/5] Report preview...')
    print()
    print('=' * 70)
    print('REPORT PREVIEW')
    print('=' * 70)
    with open(report_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            if i == 0:
                print('COLUMNS:', ', '.join(row[:6]), '...')
                print('-' * 70)
            elif i <= 10:
                print(f'{row[0][:20]:<20} | {row[5]:<10} | {row[6]:<10} | {row[7]:<10} | Cam: {row[11]}')

    print()
    print('=' * 70)
    print('DONE! Report saved to:', report_file)
    print('=' * 70)

if __name__ == '__main__':
    main()
