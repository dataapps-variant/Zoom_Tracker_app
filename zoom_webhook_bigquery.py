"""
ZOOM WEBHOOK LISTENER - GCP CLOUD RUN + GCS + BIGQUERY
=======================================================

WITH AUTOMATED SCOUT BOT FOR ROOM NAME MAPPING!

WORKFLOW:
1. Receives webhook events from Zoom
2. Scout bot automatically maps room_uuid → room_name
3. Writes raw JSON to GCS bucket (backup/archive)
4. Streams to BigQuery with room names!

SCOUT BOT WORKFLOW:
1. Meeting starts, breakout rooms created
2. Scout bot joins meeting
3. Call POST /scout/start {"meeting_id": "xxx"}
4. Scout automatically visits all 66 rooms via API
5. Webhook captures room_uuid → mapped to room_name
6. All events now have room names!
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google.cloud import bigquery
from google.cloud import storage
from datetime import datetime
import threading
import requests
import hmac
import hashlib
import json
import time
import os
import uuid as uuid_lib

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Set static folder to React build's static folder
REACT_BUILD_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'breakout-calibrator', 'build', 'static')
app = Flask(__name__, static_folder=REACT_BUILD_STATIC, static_url_path='/static')
CORS(app)  # Enable CORS for Zoom SDK app

# Zoom Webhook
ZOOM_WEBHOOK_SECRET = os.environ.get('ZOOM_WEBHOOK_SECRET', 'HyA8GYp6Spy9WWSjW4_pjA')

# Zoom API (Server-to-Server OAuth) - For Scout Bot
ZOOM_ACCOUNT_ID = os.environ.get('ZOOM_ACCOUNT_ID', 'xhKbAsmnSM6pNYYYurmqIA')
ZOOM_CLIENT_ID = os.environ.get('ZOOM_CLIENT_ID', 'TqtBGqTAS3W1Jgf9a41w')
ZOOM_CLIENT_SECRET = os.environ.get('ZOOM_CLIENT_SECRET', 'l7Rf39ydC1oidKNR3p083X9NTNlWbm5Y')

# Scout Bot Identification
SCOUT_BOT_EMAIL = os.environ.get('SCOUT_BOT_EMAIL', 'scout@yourdomain.com')
SCOUT_BOT_NAME = os.environ.get('SCOUT_BOT_NAME', 'Scout Bot')
SCOUT_MOVE_DELAY = float(os.environ.get('SCOUT_MOVE_DELAY', 3))  # Seconds between moves
SCOUT_AUTO_START = os.environ.get('SCOUT_AUTO_START', 'true').lower() == 'true'  # Auto-start mapping
SCOUT_AUTO_DELAY = float(os.environ.get('SCOUT_AUTO_DELAY', 30))  # Wait seconds before auto-start

# GCP Configuration
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'your-project-id')
GCS_BUCKET = os.environ.get('GCS_BUCKET', 'zoom-tracker-data')
GCS_RAW_PREFIX = os.environ.get('GCS_RAW_PREFIX', 'raw')

# BigQuery Configuration
BQ_DATASET = os.environ.get('BQ_DATASET', 'zoom_tracker')
BQ_TABLE = os.environ.get('BQ_TABLE', 'raw_events')
BQ_MAPPING_TABLE = os.environ.get('BQ_MAPPING_TABLE', 'room_mappings')

# Clients (initialized lazily)
bq_client = None
gcs_client = None

# In-memory event storage
events_store = []
MAX_EVENTS = 5000

def get_bq_client():
    global bq_client
    if bq_client is None:
        bq_client = bigquery.Client(project=GCP_PROJECT_ID)
    return bq_client

def get_gcs_client():
    global gcs_client
    if gcs_client is None:
        gcs_client = storage.Client(project=GCP_PROJECT_ID)
    return gcs_client

def store_event(data):
    global events_store
    events_store.append(data)
    if len(events_store) > MAX_EVENTS:
        events_store = events_store[-MAX_EVENTS:]


# ==============================================================================
# AUTOMATED SCOUT BOT - ROOM MAPPER
# ==============================================================================

class AutoRoomMapper:
    """
    Automated room mapping using Zoom API + Webhooks

    HOW IT WORKS:
    1. GET /meetings/{id}/breakout_rooms → returns room_id + room_name (from API)
    2. Move scout to room via API using room_id
    3. Webhook fires with room_uuid when scout enters
    4. Map: room_name (from API) → room_uuid (from webhook)

    WHY THIS WORKS:
    - Zoom API gives us room NAMES but uses different IDs
    - Webhook gives us room_uuid but NO names
    - Scout bot bridges the gap by visiting each room
    """

    def __init__(self):
        self.reset()
        self.access_token = None
        self.token_expires = 0

    def reset(self):
        """Reset for new meeting"""
        self.uuid_to_name = {}      # breakout_room_uuid → "Room Name"
        self.name_to_uuid = {}      # "Room Name" → breakout_room_uuid
        self.api_id_to_name = {}    # API room_id → "Room Name"

        self.meeting_id = None
        self.meeting_uuid = None
        self.scout_user_id = None
        self.rooms_from_api = []
        self.current_mapping_room = None
        self.mapping_in_progress = False
        self.mapping_complete = False
        self.mapping_log = []
        self.started_at = None
        self.completed_at = None

        print("[RoomMapper] Reset for new meeting")

    # --- Zoom API Methods ---

    def get_access_token(self):
        """Get OAuth token (cached)"""
        now = time.time()
        if self.access_token and now < self.token_expires - 60:
            return self.access_token

        if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET]):
            raise ValueError("Zoom API credentials not set. Configure ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET")

        url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={ZOOM_ACCOUNT_ID}"
        response = requests.post(
            url,
            auth=(ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )

        if response.status_code != 200:
            raise Exception(f"Token error: {response.text}")

        data = response.json()
        self.access_token = data['access_token']
        self.token_expires = now + data.get('expires_in', 3600)
        print("[RoomMapper] Got access token")
        return self.access_token

    def api_get_breakout_rooms(self, meeting_id):
        """Get breakout rooms from Zoom API - returns room names!"""
        token = self.get_access_token()

        # First try the dedicated breakout_rooms endpoint
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/breakout_rooms"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'})

        if response.status_code == 200:
            rooms = response.json().get('breakout_rooms', [])
            if rooms:
                print(f"[RoomMapper] API /breakout_rooms returned {len(rooms)} rooms")
                return rooms

        # Fallback: Get rooms from meeting details settings
        print(f"[RoomMapper] /breakout_rooms not available, trying meeting details...")
        url2 = f"https://api.zoom.us/v2/meetings/{meeting_id}"
        response2 = requests.get(url2, headers={'Authorization': f'Bearer {token}'})

        if response2.status_code != 200:
            raise Exception(f"API error: {response2.status_code} - {response2.text}")

        meeting_data = response2.json()
        settings = meeting_data.get('settings', {})
        breakout_config = settings.get('breakout_room', {})
        rooms_list = breakout_config.get('rooms', [])

        if not rooms_list:
            raise Exception("No breakout rooms found in meeting settings")

        # Convert to standard format with id and name
        rooms = []
        for i, room in enumerate(rooms_list):
            rooms.append({
                'id': str(i),  # Use index as ID since we don't have real IDs
                'name': room.get('name', f'Room {i+1}'),
                'participants': room.get('participants', [])
            })

        print(f"[RoomMapper] Meeting settings returned {len(rooms)} rooms")
        return rooms

    def api_get_live_meeting(self, meeting_id):
        """
        Get live meeting participants

        NOTE: Different APIs return different participant ID formats!
        - Dashboard API: requires Business+ account
        - Live meeting API: /meetings/{id}/participants (for in-progress meetings)
        """
        token = self.get_access_token()
        participants = []

        # Method 1: Live meeting participants (works during meeting)
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/participants"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'})

        if response.status_code == 200:
            participants = response.json().get('participants', [])
            if participants:
                print(f"[RoomMapper] Found {len(participants)} participants via live API")
                return participants

        # Method 2: Dashboard API (requires Business+ account)
        url = f"https://api.zoom.us/v2/metrics/meetings/{meeting_id}/participants"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'})

        if response.status_code == 200:
            participants = response.json().get('participants', [])
            if participants:
                print(f"[RoomMapper] Found {len(participants)} participants via dashboard API")
                return participants

        # Method 3: Past meeting (if meeting just started)
        url = f"https://api.zoom.us/v2/past_meetings/{meeting_id}/participants"
        response = requests.get(url, headers={'Authorization': f'Bearer {token}'})

        if response.status_code == 200:
            participants = response.json().get('participants', [])
            print(f"[RoomMapper] Found {len(participants)} participants via past API")
            return participants

        print(f"[RoomMapper] Could not get participants. Last response: {response.status_code}")
        return []

    def api_move_to_room(self, meeting_id, room_id, user_id):
        """
        Move participant to breakout room via API

        Uses: PUT /meetings/{meetingId}/breakout_rooms/{breakoutRoomId}/participants
        """
        token = self.get_access_token()

        # Method 1: PUT to add participant to room
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/breakout_rooms/{room_id}/participants"

        response = requests.put(
            url,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            },
            json={'participants': [{'id': user_id}]}
        )

        if response.status_code in [200, 201, 204]:
            return True

        # Method 2: Try PATCH if PUT fails
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/breakout_rooms/{room_id}"
        response = requests.patch(
            url,
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            },
            json={'participants': [user_id]}
        )

        success = response.status_code in [200, 204]
        if not success:
            print(f"[RoomMapper] Move failed: {response.status_code} - {response.text}")
        return success

    def find_scout_in_meeting(self, meeting_id):
        """Find scout bot's user_id in meeting"""
        try:
            participants = self.api_get_live_meeting(meeting_id)

            for p in participants:
                email = p.get('email', '').lower()
                name = p.get('user_name', p.get('name', '')).lower()

                if SCOUT_BOT_EMAIL and email == SCOUT_BOT_EMAIL.lower():
                    user_id = p.get('id') or p.get('user_id') or p.get('participant_user_id')
                    print(f"[RoomMapper] Found scout by email: {user_id}")
                    return user_id

                if SCOUT_BOT_NAME and SCOUT_BOT_NAME.lower() in name:
                    user_id = p.get('id') or p.get('user_id') or p.get('participant_user_id')
                    print(f"[RoomMapper] Found scout by name: {user_id}")
                    return user_id

            print(f"[RoomMapper] Scout not found in {len(participants)} participants")
            return None

        except Exception as e:
            print(f"[RoomMapper] Error finding scout: {e}")
            return None

    # --- Mapping Logic ---

    def start_mapping(self, meeting_id, scout_user_id=None, mode='sequential'):
        """
        Start automated room mapping

        Modes:
        - 'sequential': Desktop automation visits rooms in order, webhook maps by sequence
        - 'api': API moves scout through rooms (may not work for all meeting types)

        1. Get rooms from API (has names!)
        2. Set up mapping mode
        3. As scout visits rooms, webhook maps UUID to room name
        """
        self.reset()
        self.meeting_id = meeting_id
        self.started_at = datetime.utcnow().isoformat()
        self.mapping_in_progress = True
        self.mapping_mode = mode
        self.sequential_index = 0  # Track which room we're mapping next

        print(f"\n{'='*60}")
        print(f"[RoomMapper] STARTING {mode.upper()} MAPPING")
        print(f"[RoomMapper] Meeting: {meeting_id}")
        print(f"{'='*60}\n")

        # Step 1: Get rooms from API
        try:
            self.rooms_from_api = self.api_get_breakout_rooms(meeting_id)
        except Exception as e:
            self.mapping_in_progress = False
            return {'error': f'Cannot get rooms: {e}'}

        if not self.rooms_from_api:
            self.mapping_in_progress = False
            return {'error': 'No breakout rooms found. Make sure rooms are created.'}

        # Build API ID → Name mapping
        for room in self.rooms_from_api:
            self.api_id_to_name[room['id']] = room['name']

        print(f"[RoomMapper] Found {len(self.rooms_from_api)} rooms:")
        for room in self.rooms_from_api[:5]:  # Show first 5
            print(f"  - {room['name']}")
        if len(self.rooms_from_api) > 5:
            print(f"  ... and {len(self.rooms_from_api) - 5} more")

        # Sequential mode: Just wait for webhooks, user runs desktop automation
        if mode == 'sequential':
            print(f"\n[RoomMapper] SEQUENTIAL MODE - Ready for desktop automation!")
            print(f"[RoomMapper] Run 'python scout_full_auto.py' to visit rooms in order")
            print(f"[RoomMapper] Each room visit will be mapped automatically\n")

            return {
                'status': 'ready',
                'mode': 'sequential',
                'meeting_id': meeting_id,
                'total_rooms': len(self.rooms_from_api),
                'instructions': 'Run desktop automation (scout_full_auto.py) to visit rooms in order from top to bottom. Each visit will be mapped.',
                'rooms_preview': [r['name'] for r in self.rooms_from_api[:5]]
            }

        # API mode: Find scout and move via API
        if scout_user_id:
            self.scout_user_id = scout_user_id
        else:
            self.scout_user_id = self.find_scout_in_meeting(meeting_id)

        if not self.scout_user_id:
            self.mapping_in_progress = False
            return {'error': f'Scout bot not found. Make sure "{SCOUT_BOT_NAME}" or "{SCOUT_BOT_EMAIL}" has joined the meeting.'}

        print(f"[RoomMapper] Scout user_id: {self.scout_user_id}")

        # Step 3: Start moving through rooms (background thread)
        thread = threading.Thread(target=self._run_mapping_sequence, daemon=True)
        thread.start()

        return {
            'status': 'started',
            'mode': 'api',
            'meeting_id': meeting_id,
            'total_rooms': len(self.rooms_from_api),
            'scout_user_id': self.scout_user_id,
            'estimated_time': f"{len(self.rooms_from_api) * SCOUT_MOVE_DELAY} seconds"
        }

    def _run_mapping_sequence(self):
        """Background: Move scout through all rooms"""
        print(f"\n[RoomMapper] Starting mapping sequence ({len(self.rooms_from_api)} rooms)...")

        for i, room in enumerate(self.rooms_from_api):
            room_id = room['id']
            room_name = room['name']

            print(f"\n[RoomMapper] [{i+1}/{len(self.rooms_from_api)}] → {room_name}")

            # Track which room we're mapping
            self.current_mapping_room = {
                'api_id': room_id,
                'name': room_name,
                'index': i + 1
            }

            # Move scout via API
            success = self.api_move_to_room(self.meeting_id, room_id, self.scout_user_id)

            self.mapping_log.append({
                'room_name': room_name,
                'api_id': room_id,
                'move_sent': datetime.utcnow().isoformat(),
                'success': success
            })

            if success:
                print(f"[RoomMapper] ✓ Move sent")
            else:
                print(f"[RoomMapper] ✗ Move failed")

            # Wait for webhook to fire before next move
            time.sleep(SCOUT_MOVE_DELAY)

        # Complete!
        self.mapping_in_progress = False
        self.mapping_complete = True
        self.completed_at = datetime.utcnow().isoformat()
        self.current_mapping_room = None

        print(f"\n{'='*60}")
        print(f"[RoomMapper] MAPPING COMPLETE!")
        print(f"[RoomMapper] Mapped {len(self.uuid_to_name)}/{len(self.rooms_from_api)} rooms")
        print(f"{'='*60}\n")

        # Save to BigQuery
        self._save_mapping_to_bigquery()

    def scout_entered_room(self, room_uuid, meeting_uuid=None):
        """
        Called by webhook when scout enters a room

        Sequential mode: Map uuid to next room in list (visits must be in order!)
        API mode: Map uuid to current_mapping_room (set by API movement)
        """
        if meeting_uuid:
            self.meeting_uuid = meeting_uuid

        # Already mapped? Return existing name
        if room_uuid in self.uuid_to_name:
            return self.uuid_to_name[room_uuid]

        # Check if mapping is active
        if not self.mapping_in_progress:
            print(f"[RoomMapper] Scout event but no mapping in progress")
            return None

        # Get room name based on mode
        room_name = None

        if getattr(self, 'mapping_mode', 'api') == 'sequential':
            # Sequential mode: use index
            idx = getattr(self, 'sequential_index', 0)
            if idx < len(self.rooms_from_api):
                room_name = self.rooms_from_api[idx]['name']
                self.sequential_index = idx + 1
                print(f"[RoomMapper] Sequential mode: room {idx + 1}/{len(self.rooms_from_api)}")
            else:
                print(f"[RoomMapper] All rooms mapped!")
                self.mapping_in_progress = False
                self.mapping_complete = True
                self.completed_at = datetime.utcnow().isoformat()
                self._save_mapping_to_bigquery()
                return None
        else:
            # API mode: use current_mapping_room
            if not self.current_mapping_room:
                print(f"[RoomMapper] API mode but no current room set")
                return None
            room_name = self.current_mapping_room['name']

        if not room_name:
            return None

        # Create mapping!
        self.uuid_to_name[room_uuid] = room_name
        self.name_to_uuid[room_name] = room_uuid

        print(f"[RoomMapper] * MAPPED: {room_name} = {room_uuid[:20]}...")

        # Update log
        self.mapping_log.append({
            'room_name': room_name,
            'room_uuid': room_uuid,
            'mapped_at': datetime.utcnow().isoformat()
        })

        # Check if complete
        if len(self.uuid_to_name) >= len(self.rooms_from_api):
            print(f"\n{'='*60}")
            print(f"[RoomMapper] MAPPING COMPLETE!")
            print(f"[RoomMapper] Mapped {len(self.uuid_to_name)}/{len(self.rooms_from_api)} rooms")
            print(f"{'='*60}\n")
            self.mapping_in_progress = False
            self.mapping_complete = True
            self.completed_at = datetime.utcnow().isoformat()
            self._save_mapping_to_bigquery()

        return room_name

    def _save_mapping_to_bigquery(self):
        """Save room mappings to BigQuery"""
        try:
            client = get_bq_client()
            table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_MAPPING_TABLE}"

            rows = [{
                'meeting_id': str(self.meeting_id),
                'meeting_uuid': self.meeting_uuid or '',
                'room_uuid': room_uuid,
                'room_name': room_name,
                'mapped_at': datetime.utcnow().isoformat(),
                'mapping_date': datetime.utcnow().strftime('%Y-%m-%d')
            } for room_uuid, room_name in self.uuid_to_name.items()]

            if rows:
                errors = client.insert_rows_json(table_id, rows)
                if errors:
                    print(f"[RoomMapper] BigQuery error: {errors}")
                else:
                    print(f"[RoomMapper] Saved {len(rows)} mappings to BigQuery")

        except Exception as e:
            print(f"[RoomMapper] BigQuery save error: {e}")

    # --- Lookup Methods ---

    def get_room_name(self, room_uuid):
        """Get room name for a UUID"""
        return self.uuid_to_name.get(room_uuid)

    def get_status(self):
        """Get current mapping status"""
        return {
            'meeting_id': self.meeting_id,
            'mapping_in_progress': self.mapping_in_progress,
            'mapping_complete': self.mapping_complete,
            'total_rooms': len(self.rooms_from_api),
            'rooms_mapped': len(self.uuid_to_name),
            'current_room': self.current_mapping_room,
            'started_at': self.started_at,
            'completed_at': self.completed_at
        }

    def get_mapping(self):
        """Get full mapping"""
        return {
            'uuid_to_name': self.uuid_to_name.copy(),
            'name_to_uuid': self.name_to_uuid.copy(),
            'log': self.mapping_log
        }


# Global room mapper instance
room_mapper = AutoRoomMapper()


def is_scout_bot(participant_name, participant_email):
    """Check if participant is the scout bot"""
    if participant_email and SCOUT_BOT_EMAIL:
        if participant_email.lower() == SCOUT_BOT_EMAIL.lower():
            return True
    if participant_name and SCOUT_BOT_NAME:
        if SCOUT_BOT_NAME.lower() in participant_name.lower():
            return True
    return False


# ==============================================================================
# GCS FUNCTIONS
# ==============================================================================

def write_to_gcs_individual(event_data):
    """Write event to GCS as individual file"""
    try:
        client = get_gcs_client()
        bucket = client.bucket(GCS_BUCKET)

        today = datetime.utcnow().strftime('%Y-%m-%d')
        event_id = event_data.get('event_id', str(uuid_lib.uuid4()))
        blob_path = f"{GCS_RAW_PREFIX}/{today}/{event_id}.json"

        blob = bucket.blob(blob_path)
        blob.upload_from_string(
            json.dumps(event_data, indent=2),
            content_type='application/json'
        )

        print(f"  -> GCS: {blob_path}")
        return True

    except Exception as e:
        print(f"  -> GCS Error: {e}")
        return False


# ==============================================================================
# BIGQUERY FUNCTIONS
# ==============================================================================

def write_to_bigquery(event_data):
    """Stream event to BigQuery"""
    try:
        client = get_bq_client()
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}"

        errors = client.insert_rows_json(table_id, [event_data])

        if errors:
            print(f"  -> BigQuery Error: {errors}")
            return False

        print(f"  -> BigQuery: OK")
        return True

    except Exception as e:
        print(f"  -> BigQuery Error: {e}")
        return False


# ==============================================================================
# EVENT PARSING (WITH ROOM NAME!)
# ==============================================================================

def parse_zoom_event(data, room_name=None):
    """Parse Zoom webhook with room name enrichment"""
    event = data.get('event', '')
    payload = data.get('payload', {})
    obj = payload.get('object', {})
    participant = obj.get('participant', {})

    action = 'JOIN' if 'joined' in event else 'LEAVE'

    event_ts = data.get('event_ts', 0)
    if event_ts:
        event_dt = datetime.fromtimestamp(event_ts / 1000)
        event_datetime = event_dt.isoformat()
        event_date = event_dt.strftime('%Y-%m-%d')
    else:
        event_datetime = datetime.utcnow().isoformat()
        event_date = datetime.utcnow().strftime('%Y-%m-%d')

    event_id = str(uuid_lib.uuid4())
    today = datetime.utcnow().strftime('%Y-%m-%d')
    room_uuid = obj.get('breakout_room_uuid', '')

    # Get room name from mapper if not provided
    if not room_name and room_uuid:
        room_name = room_mapper.get_room_name(room_uuid)

    return {
        'event_id': event_id,
        'event_date': event_date,
        'event_type': event,
        'event_timestamp': event_datetime,
        'meeting_id': str(obj.get('id', '')),
        'meeting_uuid': obj.get('uuid', ''),
        'participant_id': participant.get('user_id', ''),
        'participant_name': participant.get('user_name', 'Unknown'),
        'participant_email': participant.get('email', ''),
        'breakout_room_uuid': room_uuid,
        'room_name': room_name or '',  # NEW FIELD!
        'action': action,
        'raw_payload': json.dumps(data),
        'inserted_at': datetime.utcnow().isoformat(),
        'gcs_path': f"gs://{GCS_BUCKET}/{GCS_RAW_PREFIX}/{today}/{event_id}.json"
    }


def parse_video_event(data):
    """Parse video on/off events"""
    event = data.get('event', '')
    payload = data.get('payload', {})
    obj = payload.get('object', {})
    participant = obj.get('participant', {})

    action = 'VIDEO_ON' if 'video_on' in event else 'VIDEO_OFF'

    event_ts = data.get('event_ts', 0)
    if event_ts:
        event_dt = datetime.fromtimestamp(event_ts / 1000)
        event_datetime = event_dt.isoformat()
        event_date = event_dt.strftime('%Y-%m-%d')
    else:
        event_datetime = datetime.utcnow().isoformat()
        event_date = datetime.utcnow().strftime('%Y-%m-%d')

    event_id = str(uuid_lib.uuid4())
    today = datetime.utcnow().strftime('%Y-%m-%d')

    return {
        'event_id': event_id,
        'event_date': event_date,
        'event_type': event,
        'event_timestamp': event_datetime,
        'meeting_id': str(obj.get('id', '')),
        'meeting_uuid': obj.get('uuid', ''),
        'participant_id': participant.get('user_id', participant.get('id', '')),
        'participant_name': participant.get('user_name', participant.get('name', 'Unknown')),
        'participant_email': participant.get('email', ''),
        'breakout_room_uuid': '',
        'room_name': '',
        'action': action,
        'raw_payload': json.dumps(data),
        'inserted_at': datetime.utcnow().isoformat(),
        'gcs_path': f"gs://{GCS_BUCKET}/{GCS_RAW_PREFIX}/{today}/{event_id}.json"
    }


# ==============================================================================
# WEBHOOK ENDPOINTS
# ==============================================================================

@app.route('/', methods=['GET'])
def health_check():
    """Health check with scout status"""
    return jsonify({
        'status': 'running',
        'service': 'Zoom Webhook + Scout Bot Mapper',
        'config': {
            'project': GCP_PROJECT_ID,
            'bucket': GCS_BUCKET,
            'dataset': BQ_DATASET,
            'table': BQ_TABLE
        },
        'scout_bot': {
            'email': SCOUT_BOT_EMAIL,
            'name': SCOUT_BOT_NAME,
            'auto_start': SCOUT_AUTO_START,
            'auto_delay_seconds': SCOUT_AUTO_DELAY,
            'move_delay_seconds': SCOUT_MOVE_DELAY,
            'mapping_status': room_mapper.get_status()
        },
        'timestamp': datetime.utcnow().isoformat()
    }), 200


@app.route('/webhook', methods=['GET', 'POST'])
def zoom_webhook():
    """Main webhook with scout bot detection"""
    if request.method == 'GET':
        return jsonify({
            'status': 'Webhook ready',
            'scout_mapping': room_mapper.get_status()
        }), 200

    data = request.json
    event = data.get('event', '')

    print(f"\n[{datetime.utcnow()}] Event: {event}")

    # Handle Zoom URL validation
    if event == 'endpoint.url_validation':
        plain_token = data.get('payload', {}).get('plainToken', '')
        encrypted_token = hmac.new(
            key=ZOOM_WEBHOOK_SECRET.encode('utf-8'),
            msg=plain_token.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()

        print("  -> URL validation: OK")
        return jsonify({
            'plainToken': plain_token,
            'encryptedToken': encrypted_token
        }), 200

    # Capture scout ID when they join main meeting + AUTO START
    if event == 'meeting.participant_joined':
        payload = data.get('payload', {})
        obj = payload.get('object', {})
        participant = obj.get('participant', {})

        p_name = participant.get('user_name', '')
        p_email = participant.get('email', '')
        meeting_id = obj.get('id', '')

        if is_scout_bot(p_name, p_email):
            scout_user_id = participant.get('user_id') or participant.get('id')
            last_scout_info['user_id'] = scout_user_id
            last_scout_info['name'] = p_name
            last_scout_info['email'] = p_email
            last_scout_info['meeting_id'] = meeting_id
            last_scout_info['timestamp'] = datetime.utcnow().isoformat()
            print(f"  -> SCOUT JOINED MEETING! ID: {scout_user_id}")

            # AUTO-START MAPPING
            if SCOUT_AUTO_START and meeting_id and scout_user_id:
                # Don't start if already mapping
                if room_mapper.mapping_in_progress:
                    print(f"  -> Mapping already in progress, skipping auto-start")
                else:
                    print(f"  -> AUTO-START enabled! Will start mapping in {SCOUT_AUTO_DELAY}s...")

                    def auto_start_mapping():
                        time.sleep(SCOUT_AUTO_DELAY)
                        # Check again before starting
                        if room_mapper.mapping_in_progress:
                            print(f"[AUTO-START] Mapping already started, skipping")
                            return
                        print(f"\n[AUTO-START] Starting room mapping for meeting {meeting_id}")
                        result = room_mapper.start_mapping(meeting_id, scout_user_id)
                        if 'error' in result:
                            print(f"[AUTO-START] Error: {result['error']}")
                        else:
                            print(f"[AUTO-START] Mapping started! {result}")

                    thread = threading.Thread(target=auto_start_mapping, daemon=True)
                    thread.start()

    # Process breakout room events
    if 'breakout_room' in event:
        payload = data.get('payload', {})
        obj = payload.get('object', {})
        participant = obj.get('participant', {})

        participant_name = participant.get('user_name', '')
        participant_email = participant.get('email', '')
        room_uuid = obj.get('breakout_room_uuid', '')
        meeting_uuid = obj.get('uuid', '')

        # Check if this is scout bot
        if is_scout_bot(participant_name, participant_email):
            print(f"  -> SCOUT BOT detected!")

            # Update room mapping
            room_name = room_mapper.scout_entered_room(room_uuid, meeting_uuid)

            status = room_mapper.get_status()
            print(f"  -> Mapped: {room_name} ({status['rooms_mapped']}/{status['total_rooms']})")

            return jsonify({
                'status': 'scout_mapped',
                'room_uuid': room_uuid,
                'room_name': room_name,
                'progress': f"{status['rooms_mapped']}/{status['total_rooms']}"
            }), 200

        # Regular participant - parse with room name
        row_data = parse_zoom_event(data)

        print(f"  -> {row_data['action']}: {row_data['participant_name']}")
        print(f"  -> Room: {row_data['room_name'] or row_data['breakout_room_uuid'][:20]}")

        store_event(data)
        write_to_gcs_individual(row_data)
        write_to_bigquery(row_data)

    # Process video events
    if 'participant_video' in event:
        row_data = parse_video_event(data)

        video_status = 'ON' if 'video_on' in event else 'OFF'
        print(f"  -> VIDEO {video_status}: {row_data['participant_name']}")

        store_event(data)
        write_to_gcs_individual(row_data)
        write_to_bigquery(row_data)

    return jsonify({'status': 'success'}), 200


# ==============================================================================
# SCOUT BOT ENDPOINTS
# ==============================================================================

@app.route('/scout/start', methods=['POST'])
def scout_start():
    """
    START AUTOMATED ROOM MAPPING

    POST /scout/start
    {
        "meeting_id": "123456789",
        "scout_user_id": "optional"  // Auto-detects if not provided
    }

    Call this ONCE per meeting after:
    1. Breakout rooms are created
    2. Scout bot has joined the meeting

    If auto-detect fails, get scout_user_id from:
    - GET /scout/last-scout-id (captured from webhook)
    - Or check webhook logs for scout's participant_id
    """
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    scout_user_id = data.get('scout_user_id')

    if not meeting_id:
        return jsonify({'error': 'meeting_id required'}), 400

    result = room_mapper.start_mapping(meeting_id, scout_user_id)

    if 'error' in result:
        return jsonify(result), 400

    return jsonify(result), 200


# Store last seen scout ID from webhook
last_scout_info = {'user_id': None, 'name': None, 'email': None, 'timestamp': None}


@app.route('/scout/last-scout-id', methods=['GET'])
def get_last_scout_id():
    """
    Get last scout ID seen in webhook events

    Use this if auto-detection fails:
    1. Scout joins meeting (webhook fires)
    2. GET /scout/last-scout-id → returns scout's user_id
    3. POST /scout/start {"meeting_id": "xxx", "scout_user_id": "yyy"}
    """
    return jsonify(last_scout_info), 200


@app.route('/scout/status', methods=['GET'])
def scout_status():
    """Get current mapping status"""
    return jsonify(room_mapper.get_status()), 200


@app.route('/scout/mapping', methods=['GET'])
def scout_mapping():
    """Get full room mapping"""
    return jsonify(room_mapper.get_mapping()), 200


@app.route('/scout/reset', methods=['POST'])
def scout_reset():
    """Reset for new meeting"""
    room_mapper.reset()
    return jsonify({'status': 'reset', 'message': 'Room mapper reset for new meeting'}), 200


@app.route('/scout/lookup/<room_uuid>', methods=['GET'])
def scout_lookup(room_uuid):
    """Look up room name by UUID"""
    room_name = room_mapper.get_room_name(room_uuid)
    return jsonify({
        'room_uuid': room_uuid,
        'room_name': room_name,
        'found': room_name is not None
    }), 200


@app.route('/scout/learn', methods=['POST'])
def scout_learn():
    """
    Learn a webhook UUID → room name mapping

    POST /scout/learn
    {
        "webhook_uuid": "6kAkE8jOgeGj5m2DPy9/",
        "room_name": "1.1:It's Accrual World"
    }
    """
    data = request.json or {}
    webhook_uuid = data.get('webhook_uuid')
    room_name = data.get('room_name')

    if not webhook_uuid or not room_name:
        return jsonify({'error': 'webhook_uuid and room_name required'}), 400

    # Store the mapping
    room_mapper.uuid_to_name[webhook_uuid] = room_name
    room_mapper.name_to_uuid[room_name] = webhook_uuid

    print(f"[Scout] Learned: {room_name} = {webhook_uuid}")

    return jsonify({
        'success': True,
        'webhook_uuid': webhook_uuid,
        'room_name': room_name
    }), 200


@app.route('/scout/all-mappings', methods=['GET'])
def scout_all_mappings():
    """Get all known mappings including SDK mappings"""
    sdk_mappings = getattr(room_mapper, 'sdk_mappings', {})
    return jsonify({
        'uuid_to_name': room_mapper.uuid_to_name,
        'name_to_uuid': room_mapper.name_to_uuid,
        'sdk_mappings': sdk_mappings,
        'total_mappings': len(room_mapper.uuid_to_name)
    }), 200


@app.route('/scout/rooms/<meeting_id>', methods=['GET'])
def scout_rooms(meeting_id):
    """Get breakout rooms for meeting via API (preview before mapping)"""
    try:
        rooms = room_mapper.api_get_breakout_rooms(meeting_id)
        return jsonify({
            'meeting_id': meeting_id,
            'total_rooms': len(rooms),
            'rooms': [{'id': r['id'], 'name': r['name']} for r in rooms]
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==============================================================================
# ZOOM APP CALIBRATION ENDPOINTS
# ==============================================================================
# These endpoints receive calibration data from the Zoom App (React frontend)
# The Zoom App runs inside the host's Zoom client and has direct SDK access

@app.route('/calibration/start', methods=['POST'])
def calibration_start():
    """
    Receive notification that Zoom App started calibration

    POST /calibration/start
    {
        "meeting_id": "123456789",
        "meeting_uuid": "abc123...",
        "started_at": "2024-01-01T00:00:00Z"
    }
    """
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    meeting_uuid = data.get('meeting_uuid')
    started_at = data.get('started_at')

    if not meeting_id:
        return jsonify({'error': 'meeting_id required'}), 400

    # Reset room mapper for this meeting
    room_mapper.reset()
    room_mapper.meeting_id = meeting_id
    room_mapper.meeting_uuid = meeting_uuid
    room_mapper.started_at = started_at
    room_mapper.mapping_in_progress = True

    print(f"\n{'='*60}")
    print(f"[ZoomApp] CALIBRATION STARTED (from Zoom App)")
    print(f"[ZoomApp] Meeting: {meeting_id}")
    print(f"{'='*60}\n")

    return jsonify({
        'success': True,
        'message': 'Calibration session started',
        'meeting_id': meeting_id
    }), 200


@app.route('/calibration/mapping', methods=['POST'])
def calibration_mapping():
    """
    Receive room mappings from Zoom App

    POST /calibration/mapping
    {
        "meeting_id": "123456789",
        "meeting_uuid": "abc123...",
        "room_mapping": [
            {"room_uuid": "xxx", "room_name": "Math Class", "room_index": 0, "mapped_at": "..."},
            {"room_uuid": "yyy", "room_name": "Science Lab", "room_index": 1, "mapped_at": "..."}
        ],
        "completed_at": "2024-01-01T00:05:00Z"
    }
    """
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    meeting_uuid = data.get('meeting_uuid')
    room_mapping = data.get('room_mapping', [])

    if not meeting_id or not room_mapping:
        return jsonify({'error': 'meeting_id and room_mapping required'}), 400

    # Update room mapper with received mappings
    room_mapper.meeting_id = meeting_id
    room_mapper.meeting_uuid = meeting_uuid

    # Store SDK mappings with both SDK and stripped UUID formats
    for room in room_mapping:
        room_uuid = room.get('room_uuid', '')
        room_name = room.get('room_name')
        if room_uuid and room_name:
            # Store with original SDK format
            room_mapper.uuid_to_name[room_uuid] = room_name
            room_mapper.name_to_uuid[room_name] = room_uuid

            # Also store stripped version (without curly braces)
            stripped_uuid = room_uuid.replace('{', '').replace('}', '')
            room_mapper.uuid_to_name[stripped_uuid] = room_name

            # Store SDK UUID for this room name (for reverse lookup)
            if not hasattr(room_mapper, 'sdk_mappings'):
                room_mapper.sdk_mappings = {}
            room_mapper.sdk_mappings[room_name] = {
                'sdk_uuid': room_uuid,
                'room_index': room.get('room_index')
            }

    # Set mapping as complete since we have SDK data
    room_mapper.mapping_complete = True
    print(f"[ZoomApp] SDK mappings stored: {len(room_mapper.uuid_to_name)} entries")

    print(f"[ZoomApp] Received {len(room_mapping)} room mappings:")
    for room in room_mapping[:5]:
        print(f"  - {room['room_name']} -> {room['room_uuid'][:16]}...")
    if len(room_mapping) > 5:
        print(f"  ... and {len(room_mapping) - 5} more")

    # Save to BigQuery
    try:
        client = get_bq_client()
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_MAPPING_TABLE}"

        rows = [{
            'meeting_id': str(meeting_id),
            'meeting_uuid': meeting_uuid or '',
            'room_uuid': room['room_uuid'],
            'room_name': room['room_name'],
            'mapped_at': room.get('mapped_at', datetime.utcnow().isoformat()),
            'mapping_date': datetime.utcnow().strftime('%Y-%m-%d'),
            'source': 'zoom_app'
        } for room in room_mapping]

        errors = client.insert_rows_json(table_id, rows)
        if errors:
            print(f"[ZoomApp] BigQuery error: {errors}")
        else:
            print(f"[ZoomApp] Saved {len(rows)} mappings to BigQuery")

    except Exception as e:
        print(f"[ZoomApp] BigQuery save error: {e}")

    return jsonify({
        'success': True,
        'message': f'Received {len(room_mapping)} room mappings',
        'mappings_count': len(room_mapping)
    }), 200


@app.route('/calibration/complete', methods=['POST'])
def calibration_complete():
    """
    Receive notification that Zoom App completed calibration

    POST /calibration/complete
    {
        "meeting_id": "123456789",
        "meeting_uuid": "abc123...",
        "success": true,
        "total_rooms": 66,
        "mapped_rooms": 66,
        "completed_at": "2024-01-01T00:05:00Z"
    }
    """
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    success = data.get('success', False)
    total_rooms = data.get('total_rooms', 0)
    mapped_rooms = data.get('mapped_rooms', 0)
    completed_at = data.get('completed_at')

    # Update room mapper status
    room_mapper.mapping_in_progress = False
    room_mapper.mapping_complete = success
    room_mapper.completed_at = completed_at

    print(f"\n{'='*60}")
    print(f"[ZoomApp] CALIBRATION {'COMPLETE' if success else 'FAILED'}")
    print(f"[ZoomApp] Mapped {mapped_rooms}/{total_rooms} rooms")
    print(f"{'='*60}\n")

    return jsonify({
        'success': True,
        'message': 'Calibration session completed'
    }), 200


@app.route('/calibration/mappings/<meeting_id>', methods=['GET'])
def get_calibration_mappings(meeting_id):
    """
    Get existing room mappings for a meeting

    GET /calibration/mappings/123456789

    Returns room mappings from memory (or could query BigQuery)
    """
    # Check in-memory mappings first
    if room_mapper.meeting_id == meeting_id:
        mappings = [
            {'room_uuid': uuid, 'room_name': name}
            for uuid, name in room_mapper.uuid_to_name.items()
        ]
        return jsonify({
            'meeting_id': meeting_id,
            'mappings': mappings,
            'count': len(mappings),
            'source': 'memory'
        }), 200

    # Could also query BigQuery here for historical mappings
    return jsonify({
        'meeting_id': meeting_id,
        'mappings': [],
        'count': 0,
        'source': 'not_found'
    }), 200


# ==============================================================================
# DATA ENDPOINTS
# ==============================================================================

@app.route('/data', methods=['GET'])
def get_data():
    """Return stored events (with room names if mapped)"""
    return jsonify({
        'status': 'success',
        'total_events': len(events_store),
        'room_mapping': room_mapper.get_status(),
        'events': events_store
    }), 200


@app.route('/clear', methods=['POST'])
def clear_data():
    """Clear stored events"""
    global events_store
    count = len(events_store)
    events_store = []
    return jsonify({'status': 'cleared', 'events_cleared': count}), 200


# ==============================================================================
# TEST ENDPOINTS
# ==============================================================================

@app.route('/test-gcs', methods=['GET'])
def test_gcs():
    try:
        client = get_gcs_client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob('test/connection_test.txt')
        blob.upload_from_string(f'Test at {datetime.utcnow().isoformat()}')
        return jsonify({'status': 'GCS OK', 'bucket': GCS_BUCKET}), 200
    except Exception as e:
        return jsonify({'status': 'GCS Error', 'error': str(e)}), 500


@app.route('/test-bq', methods=['GET'])
def test_bq():
    try:
        client = get_bq_client()
        query = f"SELECT COUNT(*) as count FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}`"
        result = list(client.query(query).result())
        return jsonify({
            'status': 'BigQuery OK',
            'table': f'{BQ_DATASET}.{BQ_TABLE}',
            'row_count': result[0]['count']
        }), 200
    except Exception as e:
        return jsonify({'status': 'BigQuery Error', 'error': str(e)}), 500


@app.route('/test-zoom-api', methods=['GET'])
def test_zoom_api():
    """Test Zoom API connection"""
    try:
        room_mapper.get_access_token()
        return jsonify({
            'status': 'Zoom API OK',
            'message': 'Successfully obtained access token'
        }), 200
    except Exception as e:
        return jsonify({'status': 'Zoom API Error', 'error': str(e)}), 500


@app.route('/debug-api/<meeting_id>', methods=['GET'])
def debug_api(meeting_id):
    """Debug Zoom API endpoints"""
    results = {}

    try:
        token = room_mapper.get_access_token()
        headers = {'Authorization': f'Bearer {token}'}

        # Test 1: Get meeting details
        url1 = f"https://api.zoom.us/v2/meetings/{meeting_id}"
        r1 = requests.get(url1, headers=headers)
        results['meeting_details'] = {
            'url': url1,
            'status': r1.status_code,
            'response': r1.json() if r1.status_code == 200 else r1.text
        }

        # Test 2: Get breakout rooms (standard endpoint)
        url2 = f"https://api.zoom.us/v2/meetings/{meeting_id}/breakout_rooms"
        r2 = requests.get(url2, headers=headers)
        results['breakout_rooms'] = {
            'url': url2,
            'status': r2.status_code,
            'response': r2.json() if r2.status_code == 200 else r2.text
        }

        # Test 3: Live meeting participants
        url3 = f"https://api.zoom.us/v2/meetings/{meeting_id}/participants"
        r3 = requests.get(url3, headers=headers)
        results['live_participants'] = {
            'url': url3,
            'status': r3.status_code,
            'response': r3.json() if r3.status_code == 200 else r3.text[:500] if len(r3.text) > 500 else r3.text
        }

        # Test 4: Dashboard API (requires Business+)
        url4 = f"https://api.zoom.us/v2/metrics/meetings/{meeting_id}/participants"
        r4 = requests.get(url4, headers=headers)
        results['dashboard_participants'] = {
            'url': url4,
            'status': r4.status_code,
            'response': r4.json() if r4.status_code == 200 else r4.text[:500] if len(r4.text) > 500 else r4.text
        }

        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==============================================================================
# ZOOM SDK APP (STATIC FILES)
# ==============================================================================

ZOOM_APP_BUILD_PATH = os.path.join(os.path.dirname(__file__), 'breakout-calibrator', 'build')

@app.route('/app')
@app.route('/app/')
def serve_zoom_app():
    """Serve Zoom SDK app"""
    return send_from_directory(ZOOM_APP_BUILD_PATH, 'index.html')

@app.route('/app/<path:path>')
def serve_zoom_app_static(path):
    """Serve Zoom SDK app static files"""
    return send_from_directory(ZOOM_APP_BUILD_PATH, path)

# Static files are served automatically by Flask from static_folder


# ==============================================================================
# RUN SERVER
# ==============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("=" * 60)
    print("ZOOM WEBHOOK + SCOUT BOT MAPPER")
    print("=" * 60)
    print(f"Port: {port}")
    print(f"GCS Bucket: {GCS_BUCKET}")
    print(f"BigQuery: {GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}")
    print()
    print("SCOUT BOT CONFIG:")
    print(f"  Email: {SCOUT_BOT_EMAIL}")
    print(f"  Name: {SCOUT_BOT_NAME}")
    print(f"  Move Delay: {SCOUT_MOVE_DELAY}s between rooms")
    print()
    if SCOUT_AUTO_START:
        print("AUTO-START: ENABLED")
        print(f"  Scout joins meeting -> waits {SCOUT_AUTO_DELAY}s -> auto-maps all rooms")
        print(f"  Total time for 66 rooms: ~{int(SCOUT_AUTO_DELAY + 66 * SCOUT_MOVE_DELAY)}s")
    else:
        print("AUTO-START: DISABLED")
        print("  Manual trigger required: POST /scout/start")
    print()
    print("ENDPOINTS:")
    print("  GET  /              - Health check + status")
    print("  POST /scout/start   - Manual start mapping")
    print("  GET  /scout/status  - Check mapping progress")
    print("  GET  /scout/mapping - Get room name mappings")
    print("  POST /scout/reset   - Reset for new meeting")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
