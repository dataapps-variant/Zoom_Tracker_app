"""
ZOOM BREAKOUT ROOM TRACKER - GCP CLOUD RUN + BIGQUERY
======================================================
Production-ready server for tracking:
- Participant joins/leaves
- Camera ON/OFF with exact timestamps
- Room visits with duration
- QoS data collection
- Dynamic room mapping per meeting

HR Scout Bot Flow:
1. Meeting starts at 9 AM
2. HR joins as "Scout Bot"
3. Opens Zoom App -> Click calibration -> Mappings stored
4. Scout Bot can leave after calibration
5. Webhooks capture all participant activity
6. Daily report generated and emailed
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google.cloud import bigquery
from datetime import datetime, timedelta
import threading
import requests
import hmac
import hashlib
import json
import time
import os
import uuid as uuid_lib
import traceback

# ==============================================================================
# CONFIGURATION
# ==============================================================================

REACT_BUILD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'breakout-calibrator', 'build')
STATIC_PATH = os.path.join(REACT_BUILD_PATH, 'static')
app = Flask(__name__, static_folder=STATIC_PATH, static_url_path='/app/static')
CORS(app, resources={r"/*": {"origins": "*", "methods": ["GET", "POST", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization"]}})


# Headers for Zoom Apps - allow embedding
@app.after_request
def add_zoom_headers(response):
    # Do NOT set X-Frame-Options - allow any site to embed
    # CORS headers
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return response

# Zoom Credentials
ZOOM_WEBHOOK_SECRET = os.environ.get('ZOOM_WEBHOOK_SECRET', 'HyA8GYp6Spy9WWSjW4_pjA')
ZOOM_ACCOUNT_ID = os.environ.get('ZOOM_ACCOUNT_ID', 'xhKbAsmnSM6pNYYYurmqIA')
ZOOM_CLIENT_ID = os.environ.get('ZOOM_CLIENT_ID', 'TqtBGqTAS3W1Jgf9a41w')
ZOOM_CLIENT_SECRET = os.environ.get('ZOOM_CLIENT_SECRET', '')

# Scout Bot Configuration
SCOUT_BOT_NAME = os.environ.get('SCOUT_BOT_NAME', 'Scout Bot')
SCOUT_BOT_EMAIL = os.environ.get('SCOUT_BOT_EMAIL', '')

# GCP Configuration
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', '')
BQ_DATASET = os.environ.get('BQ_DATASET', 'breakout_room_calibrator')

# BigQuery Tables
BQ_EVENTS_TABLE = 'participant_events'
BQ_MAPPINGS_TABLE = 'room_mappings'
BQ_CAMERA_TABLE = 'camera_events'
BQ_QOS_TABLE = 'qos_data'

# Email Configuration
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
REPORT_EMAIL_FROM = os.environ.get('REPORT_EMAIL_FROM', 'reports@yourdomain.com')
REPORT_EMAIL_TO = os.environ.get('REPORT_EMAIL_TO', '')

# Clients
bq_client = None

def get_bq_client():
    global bq_client
    if bq_client is None:
        bq_client = bigquery.Client(project=GCP_PROJECT_ID)
    return bq_client

# ==============================================================================
# IN-MEMORY STATE (Per Meeting - Reset on new meeting)
# ==============================================================================

class MeetingState:
    """State for current meeting - resets when new meeting starts"""

    def __init__(self):
        self.previous_meeting_uuid = None  # Store previous meeting for QoS collection
        self.previous_meeting_id = None
        self.reset()

    def reset(self):
        self.meeting_id = None
        self.meeting_uuid = None
        self.meeting_date = None
        self.uuid_to_name = {}  # room_uuid -> room_name
        self.name_to_uuid = {}  # room_name -> room_uuid
        self.calibration_complete = False
        self.calibrated_at = None
        self.participant_states = {}  # participant_id -> {camera_on: bool, last_room: str, ...}
        self.scout_bot_current_room = None  # Track current room during calibration
        self.pending_room_moves = []  # Queue of (room_name, timestamp) for Scout Bot moves
        self.calibration_in_progress = False  # Flag to track if calibration is active
        # Calibration participant info (for "Move Myself" mode)
        self.calibration_mode = 'scout_bot'  # 'scout_bot' or 'self'
        self.calibration_participant_name = None  # Name of participant doing calibration
        self.calibration_participant_uuid = None  # UUID of participant doing calibration
        print("[MeetingState] Reset for new meeting")

    def set_meeting(self, meeting_id, meeting_uuid=None):
        """Set current meeting, reset if different from previous"""
        today = datetime.utcnow().strftime('%Y-%m-%d')

        # Check if this is a new meeting
        if self.meeting_id != meeting_id or self.meeting_date != today:
            # Store previous meeting info for QoS collection
            old_uuid = self.meeting_uuid
            old_id = self.meeting_id

            print(f"[MeetingState] New meeting detected: {meeting_id}")

            # Trigger QoS collection for previous meeting (if exists)
            if old_uuid and old_uuid != meeting_uuid:
                print(f"[MeetingState] Previous meeting UUID: {old_uuid} - triggering QoS collection")
                self.previous_meeting_uuid = old_uuid
                self.previous_meeting_id = old_id
                # Trigger async QoS collection
                self._collect_previous_meeting_qos(old_uuid, old_id)

            self.reset()
            self.meeting_id = meeting_id
            self.meeting_uuid = meeting_uuid
            self.meeting_date = today

            # Delete old mappings from BigQuery for this meeting date
            self._delete_old_mappings(today)

        if meeting_uuid and not self.meeting_uuid:
            self.meeting_uuid = meeting_uuid

    def _collect_previous_meeting_qos(self, meeting_uuid, meeting_id):
        """Collect QoS data AND camera data for previous meeting in background thread"""
        def collect_qos_async():
            print(f"[AutoQoS] Starting automatic QoS + Camera collection for previous meeting: {meeting_uuid}")
            time.sleep(30)  # Wait 30 seconds for Zoom to finalize data

            collected_count = 0
            error_count = 0

            # First, collect camera QoS data (Dashboard API - only available shortly after meeting)
            camera_data_map = {}
            try:
                print(f"[AutoQoS] Collecting camera data via Dashboard QoS API...")
                camera_participants = zoom_api.get_meeting_participants_qos(meeting_id or meeting_uuid)
                for cp in camera_participants:
                    user_name = cp.get('user_name', '')
                    email = cp.get('email', '')
                    camera_on_count = cp.get('camera_on_count', 0)
                    # Key by name+email for matching
                    key = f"{user_name}|{email}".lower()
                    camera_data_map[key] = camera_on_count
                print(f"[AutoQoS] Got camera data for {len(camera_data_map)} participants")
            except Exception as ce:
                print(f"[AutoQoS] Camera collection error (non-fatal): {ce}")

            try:
                participants = zoom_api.get_past_meeting_participants(meeting_uuid)

                if not participants and meeting_id:
                    participants = zoom_api.get_past_meeting_participants(meeting_id)

                if not participants:
                    print(f"[AutoQoS] No participants found for previous meeting")
                    return

                print(f"[AutoQoS] Processing {len(participants)} participants from previous meeting...")

                for p in participants:
                    try:
                        participant_id = safe_str(
                            p.get('user_id') or p.get('id') or p.get('participant_user_id'),
                            default='unknown'
                        )
                        participant_name = safe_str(
                            p.get('name') or p.get('user_name'),
                            default='Unknown'
                        )
                        participant_email = safe_str(
                            p.get('user_email') or p.get('email'),
                            default=''
                        )
                        duration_seconds = safe_int(p.get('duration', 0))
                        duration_minutes = duration_seconds // 60 if duration_seconds > 0 else 0

                        # Look up camera data
                        camera_key = f"{participant_name}|{participant_email}".lower()
                        camera_on_count = camera_data_map.get(camera_key, 0)

                        qos_data = {
                            'qos_id': str(uuid_lib.uuid4()),
                            'meeting_uuid': safe_str(meeting_uuid),
                            'participant_id': participant_id,
                            'participant_name': participant_name,
                            'participant_email': participant_email,
                            'join_time': safe_str(p.get('join_time', '')),
                            'leave_time': safe_str(p.get('leave_time', '')),
                            'duration_minutes': duration_minutes,
                            'attentiveness_score': str(p.get('attentiveness_score', '')),
                            'camera_on_count': camera_on_count,
                            'recorded_at': datetime.utcnow().isoformat(),
                            'event_date': datetime.utcnow().strftime('%Y-%m-%d')
                        }

                        if insert_qos_data(qos_data):
                            collected_count += 1
                        else:
                            error_count += 1

                    except Exception as pe:
                        error_count += 1
                        print(f"[AutoQoS] Error processing participant: {pe}")

                print(f"[AutoQoS] Collection complete: {collected_count} success, {error_count} errors")

            except Exception as e:
                print(f"[AutoQoS] Error: {e}")
                traceback.print_exc()

        thread = threading.Thread(target=collect_qos_async, daemon=True)
        thread.start()
        print(f"[AutoQoS] Background thread started for previous meeting QoS")

    def _delete_old_mappings(self, date):
        """Delete old mappings from BigQuery for today"""
        try:
            client = get_bq_client()
            query = f"""
            DELETE FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_MAPPINGS_TABLE}`
            WHERE mapping_date = '{date}'
            """
            client.query(query).result()
            print(f"[MeetingState] Deleted old mappings for {date}")
        except Exception as e:
            print(f"[MeetingState] Error deleting old mappings: {e}")

    def load_mappings_from_bigquery(self, date=None):
        """Load today's mappings from BigQuery (after server restart)"""
        if date is None:
            date = datetime.utcnow().strftime('%Y-%m-%d')

        try:
            client = get_bq_client()
            query = f"""
            SELECT room_uuid, room_name, meeting_id
            FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_MAPPINGS_TABLE}`
            WHERE mapping_date = '{date}'
            ORDER BY mapped_at DESC
            """
            results = client.query(query).result()

            count = 0
            for row in results:
                room_uuid = row.room_uuid
                room_name = row.room_name
                if room_uuid and room_name:
                    self.uuid_to_name[room_uuid] = room_name
                    self.name_to_uuid[room_name] = room_uuid
                    # Also store without braces
                    stripped = room_uuid.replace('{', '').replace('}', '')
                    if stripped != room_uuid:
                        self.uuid_to_name[stripped] = room_name
                    count += 1

                    if not self.meeting_id and row.meeting_id:
                        self.meeting_id = row.meeting_id

            if count > 0:
                self.calibration_complete = True
                self.meeting_date = date
                print(f"[MeetingState] Loaded {count} mappings from BigQuery for {date}")

            return count
        except Exception as e:
            print(f"[MeetingState] Error loading mappings: {e}")
            return 0

    def add_room_mapping(self, room_uuid, room_name):
        """Add a room mapping"""
        self.uuid_to_name[room_uuid] = room_name
        self.name_to_uuid[room_name] = room_uuid

        # Also store without braces
        stripped = room_uuid.replace('{', '').replace('}', '')
        if stripped != room_uuid:
            self.uuid_to_name[stripped] = room_name

        # Store lowercase version too
        self.uuid_to_name[room_uuid.lower()] = room_name
        self.uuid_to_name[stripped.lower()] = room_name

    def add_webhook_room_mapping(self, webhook_uuid, room_name):
        """Add a webhook UUID to room name mapping (different format from SDK)"""
        if webhook_uuid and room_name:
            self.uuid_to_name[webhook_uuid] = room_name
            # Also store first 8 chars as key
            short_key = webhook_uuid[:8] if len(webhook_uuid) >= 8 else webhook_uuid
            if short_key not in self.uuid_to_name:
                self.uuid_to_name[short_key] = room_name

    def get_room_name(self, room_uuid):
        """Get room name from UUID"""
        if not room_uuid:
            return None

        # Try direct lookup
        if room_uuid in self.uuid_to_name:
            return self.uuid_to_name[room_uuid]

        # Try without braces
        stripped = room_uuid.replace('{', '').replace('}', '')
        return self.uuid_to_name.get(stripped)

    def get_participant_state(self, participant_id):
        """Get or create participant state"""
        if participant_id not in self.participant_states:
            self.participant_states[participant_id] = {
                'camera_on': False,
                'camera_on_since': None,
                'current_room': None,
                'joined_at': None
            }
        return self.participant_states[participant_id]

    def update_camera_state(self, participant_id, camera_on, timestamp):
        """Update camera state for participant"""
        state = self.get_participant_state(participant_id)

        if camera_on and not state['camera_on']:
            # Camera turned ON
            state['camera_on'] = True
            state['camera_on_since'] = timestamp
        elif not camera_on and state['camera_on']:
            # Camera turned OFF
            state['camera_on'] = False
            state['camera_on_since'] = None

        return state


# Global meeting state
meeting_state = MeetingState()


_initialized = False

def init_meeting_state():
    """Initialize meeting state - load today's mappings from BigQuery"""
    global _initialized
    if _initialized:
        return

    try:
        count = meeting_state.load_mappings_from_bigquery()
        if count > 0:
            print(f"[Startup] Restored {count} room mappings from BigQuery")
        else:
            print(f"[Startup] No existing mappings found for today")
        _initialized = True
    except Exception as e:
        print(f"[Startup] Could not load mappings: {e}")


# Initialize on module load (works with gunicorn)
# Delayed init - will run on first request if BigQuery not ready at startup
@app.before_request
def ensure_initialized():
    """Ensure mappings are loaded before handling requests"""
    global _initialized
    if not _initialized:
        init_meeting_state()


# ==============================================================================
# BIGQUERY FUNCTIONS
# ==============================================================================

def validate_and_clean_event(event_data, required_fields=None):
    """
    Validate and clean event data before BigQuery insert.
    Ensures all fields have proper types and no None values.
    """
    if required_fields is None:
        required_fields = ['event_id', 'event_type']

    cleaned = {}
    for key, value in event_data.items():
        # Convert None to appropriate defaults
        if value is None:
            if key.endswith('_id') or key.endswith('_uuid') or key.endswith('_name') or key.endswith('_email'):
                cleaned[key] = ''
            elif key.endswith('_seconds') or key.endswith('_minutes') or key.endswith('_count'):
                cleaned[key] = 0
            elif key == 'camera_on':
                cleaned[key] = False
            else:
                cleaned[key] = ''
        # Ensure strings are actually strings
        elif isinstance(value, str):
            cleaned[key] = value.strip()
        # Ensure numbers are proper type
        elif isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)

    # Validate required fields exist
    for field in required_fields:
        if field not in cleaned or not cleaned[field]:
            print(f"[Validation] Missing required field: {field}")
            return None

    return cleaned


def insert_participant_event(event_data):
    """Insert participant event into BigQuery with validation"""
    try:
        # Validate and clean data
        required = ['event_id', 'event_type', 'event_timestamp', 'event_date',
                   'meeting_id', 'participant_id', 'participant_name', 'inserted_at']
        cleaned_data = validate_and_clean_event(event_data, required)

        if not cleaned_data:
            print(f"[BigQuery] Validation failed for participant event")
            print(f"[BigQuery] Raw data: {json.dumps(event_data, indent=2)}")
            return False

        client = get_bq_client()
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_EVENTS_TABLE}"

        errors = client.insert_rows_json(table_id, [cleaned_data])
        if errors:
            print(f"[BigQuery] Insert error: {errors}")
            print(f"[BigQuery] Failed data: {json.dumps(cleaned_data, indent=2)}")
            return False

        return True
    except Exception as e:
        print(f"[BigQuery] Error: {e}")
        traceback.print_exc()
        return False


def insert_camera_event(event_data):
    """Insert camera on/off event into BigQuery with validation"""
    try:
        # Validate and clean data
        required = ['event_id', 'event_type', 'event_timestamp', 'event_date',
                   'meeting_id', 'participant_id', 'participant_name', 'inserted_at']
        cleaned_data = validate_and_clean_event(event_data, required)

        if not cleaned_data:
            print(f"[BigQuery] Validation failed for camera event")
            return False

        # Ensure duration_seconds is int or None
        if 'duration_seconds' in cleaned_data:
            val = cleaned_data['duration_seconds']
            if val is None or val == '':
                cleaned_data['duration_seconds'] = None  # BigQuery accepts NULL for INT64
            else:
                try:
                    cleaned_data['duration_seconds'] = int(val)
                except (ValueError, TypeError):
                    cleaned_data['duration_seconds'] = None

        client = get_bq_client()
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_CAMERA_TABLE}"

        errors = client.insert_rows_json(table_id, [cleaned_data])
        if errors:
            print(f"[BigQuery] Camera event error: {errors}")
            print(f"[BigQuery] Failed data: {json.dumps(cleaned_data, indent=2, default=str)}")
            return False

        return True
    except Exception as e:
        print(f"[BigQuery] Camera event error: {e}")
        traceback.print_exc()
        return False


def insert_room_mappings(mappings):
    """Insert room mappings into BigQuery with validation"""
    try:
        # Clean each mapping
        cleaned_mappings = []
        required = ['mapping_id', 'meeting_id', 'room_uuid', 'room_name', 'mapping_date', 'mapped_at']

        for mapping in mappings:
            cleaned = validate_and_clean_event(mapping, required)
            if cleaned:
                # Ensure room_index is int
                if 'room_index' in cleaned:
                    try:
                        cleaned['room_index'] = int(cleaned['room_index']) if cleaned['room_index'] else 0
                    except (ValueError, TypeError):
                        cleaned['room_index'] = 0
                cleaned_mappings.append(cleaned)
            else:
                print(f"[BigQuery] Skipping invalid mapping: {mapping}")

        if not cleaned_mappings:
            print(f"[BigQuery] No valid mappings to insert")
            return False

        client = get_bq_client()
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_MAPPINGS_TABLE}"

        errors = client.insert_rows_json(table_id, cleaned_mappings)
        if errors:
            print(f"[BigQuery] Mapping insert error: {errors}")
            return False

        print(f"[BigQuery] Inserted {len(cleaned_mappings)} mappings successfully")
        return True
    except Exception as e:
        print(f"[BigQuery] Mapping error: {e}")
        traceback.print_exc()
        return False


def insert_qos_data(qos_data):
    """Insert QoS data into BigQuery with validation"""
    try:
        # Validate and clean data
        required = ['qos_id', 'meeting_uuid', 'recorded_at', 'event_date']
        cleaned_data = validate_and_clean_event(qos_data, required)

        if not cleaned_data:
            print(f"[BigQuery] Validation failed for QoS data")
            print(f"[BigQuery] Raw QoS data: {json.dumps(qos_data, indent=2)}")
            return False

        # Ensure duration_minutes is int
        if 'duration_minutes' in cleaned_data:
            try:
                val = cleaned_data['duration_minutes']
                cleaned_data['duration_minutes'] = int(val) if val is not None and val != '' else 0
            except (ValueError, TypeError):
                cleaned_data['duration_minutes'] = 0

        client = get_bq_client()
        table_id = f"{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_QOS_TABLE}"

        errors = client.insert_rows_json(table_id, [cleaned_data])
        if errors:
            print(f"[BigQuery] QoS insert error: {errors}")
            print(f"[BigQuery] Failed QoS data: {json.dumps(cleaned_data, indent=2)}")
            return False

        print(f"[BigQuery] QoS insert success for {cleaned_data.get('participant_name', 'unknown')}")
        return True
    except Exception as e:
        print(f"[BigQuery] QoS error: {e}")
        traceback.print_exc()
        return False


# ==============================================================================
# ZOOM API HELPERS
# ==============================================================================

class ZoomAPI:
    """Helper for Zoom API calls"""

    def __init__(self):
        self.access_token = None
        self.token_expires = 0

    def get_access_token(self):
        """Get OAuth token (cached)"""
        now = time.time()
        if self.access_token and now < self.token_expires - 60:
            return self.access_token

        if not all([ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET]):
            raise ValueError("Zoom API credentials not configured")

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
        return self.access_token

    def get_past_meeting_participants(self, meeting_uuid, page_size=300):
        """
        Get past meeting participants - includes duration and basic QoS
        NOW WITH PAGINATION SUPPORT - fetches ALL pages

        IMPORTANT: Zoom API returns 'duration' in SECONDS, not minutes!
        The caller must convert to minutes if needed.

        Returns list of participant dicts with fields:
        - id/user_id: Participant ID
        - name/user_name: Display name
        - user_email/email: Email (may be empty)
        - join_time: ISO timestamp
        - leave_time: ISO timestamp
        - duration: Duration in SECONDS (not minutes!)
        - attentiveness_score: May not be present (requires Business+ plan)
        """
        all_participants = []

        try:
            token = self.get_access_token()
            headers = {'Authorization': f'Bearer {token}'}

            # Build list of URL patterns to try (will add pagination params later)
            url_patterns = []

            # Method 1: Double-encoded UUID (required for UUIDs with / or //)
            encoded_uuid = requests.utils.quote(requests.utils.quote(meeting_uuid, safe=''), safe='')
            url_patterns.append(
                (f"https://api.zoom.us/v2/past_meetings/{encoded_uuid}/participants", "past_meetings (double-encoded)")
            )

            # Method 2: Single-encoded UUID
            encoded_uuid2 = requests.utils.quote(meeting_uuid, safe='')
            if encoded_uuid2 != encoded_uuid:
                url_patterns.append(
                    (f"https://api.zoom.us/v2/past_meetings/{encoded_uuid2}/participants", "past_meetings (single-encoded)")
                )

            # Method 3: Raw UUID (for simple meeting IDs)
            if meeting_uuid and not any(c in meeting_uuid for c in ['/', '+', '=']):
                url_patterns.append(
                    (f"https://api.zoom.us/v2/past_meetings/{meeting_uuid}/participants", "past_meetings (raw)")
                )

            # Method 4: Report API (may have more data, requires Zoom Pro+)
            url_patterns.append(
                (f"https://api.zoom.us/v2/report/meetings/{encoded_uuid2}/participants", "report API")
            )

            # Try each method with pagination
            for base_url, method_name in url_patterns:
                try:
                    all_participants = []
                    next_page_token = None
                    page_count = 0
                    max_pages = 50  # Safety limit

                    while page_count < max_pages:
                        # Build URL with pagination params
                        params = {'page_size': page_size}
                        if next_page_token:
                            params['next_page_token'] = next_page_token

                        print(f"[ZoomAPI] Trying {method_name} (page {page_count + 1})...")
                        response = requests.get(base_url, headers=headers, params=params)

                        if response.status_code == 200:
                            data = response.json()
                            participants = data.get('participants', [])

                            if participants:
                                all_participants.extend(participants)
                                print(f"[ZoomAPI] Page {page_count + 1}: got {len(participants)} participants (total: {len(all_participants)})")

                                # Check for more pages
                                next_page_token = data.get('next_page_token', '')
                                page_count += 1

                                if not next_page_token:
                                    # No more pages
                                    print(f"[ZoomAPI] SUCCESS via {method_name}: {len(all_participants)} total participants")

                                    # Log first participant for debugging
                                    if all_participants:
                                        sample = all_participants[0]
                                        print(f"[ZoomAPI] Sample participant fields: {list(sample.keys())}")
                                        duration = sample.get('duration', 'N/A')
                                        print(f"[ZoomAPI] Sample duration value: {duration} (type: {type(duration).__name__})")

                                    return all_participants
                            else:
                                # No participants on first page
                                break

                        elif response.status_code == 404:
                            print(f"[ZoomAPI] {method_name}: Meeting not found (404)")
                            break
                        elif response.status_code == 400:
                            print(f"[ZoomAPI] {method_name}: Bad request (400) - {response.text[:200]}")
                            break
                        elif response.status_code == 401:
                            print(f"[ZoomAPI] {method_name}: Unauthorized (401) - refreshing token")
                            self.access_token = None
                            self.token_expires = 0
                            token = self.get_access_token()
                            headers = {'Authorization': f'Bearer {token}'}
                            # Retry same page
                            continue
                        else:
                            print(f"[ZoomAPI] {method_name}: {response.status_code} - {response.text[:200]}")
                            break

                    # If we collected any participants, return them
                    if all_participants:
                        print(f"[ZoomAPI] SUCCESS via {method_name}: {len(all_participants)} total participants")
                        return all_participants

                except requests.exceptions.RequestException as re:
                    print(f"[ZoomAPI] {method_name}: Request error - {re}")

            print(f"[ZoomAPI] All methods failed for meeting: {meeting_uuid}")
            return []

        except Exception as e:
            print(f"[ZoomAPI] Past meeting error: {e}")
            traceback.print_exc()
            return []

    def get_meeting_participants_qos(self, meeting_id):
        """
        Get QoS data for meeting participants using Dashboard Metrics API.
        This includes video_output data which indicates camera status.

        IMPORTANT: Requires Business/Education/Enterprise plan and
        dashboard_meetings:read:admin scope.

        Returns list of participants with video_output stats.
        When camera is ON: video_output has bitrate, resolution, etc.
        When camera is OFF: video_output is empty/null
        """
        all_participants = []

        try:
            token = self.get_access_token()
            headers = {'Authorization': f'Bearer {token}'}

            # Dashboard Metrics API endpoint
            # Works for both live and past meetings (within last 30 days)
            encoded_id = requests.utils.quote(requests.utils.quote(str(meeting_id), safe=''), safe='')
            base_url = f"https://api.zoom.us/v2/metrics/meetings/{encoded_id}/participants/qos"

            next_page_token = None
            page_count = 0
            max_pages = 200  # Increased to handle large meetings (2000 participants)

            print(f"[ZoomAPI] Fetching QoS data for meeting {meeting_id}...")

            while page_count < max_pages:
                params = {'page_size': 10}  # Max 10 per page for QoS API
                if next_page_token:
                    params['next_page_token'] = next_page_token

                response = requests.get(base_url, headers=headers, params=params)

                if response.status_code == 200:
                    data = response.json()
                    participants = data.get('participants', [])

                    if participants:
                        # Extract camera status from video_output
                        for p in participants:
                            user_qos = p.get('user_qos', [])
                            camera_on_periods = []

                            for qos_entry in user_qos:
                                video_output = qos_entry.get('video_output', {})
                                if video_output and video_output.get('bitrate'):
                                    # Camera was ON during this period
                                    camera_on_periods.append({
                                        'bitrate': video_output.get('bitrate'),
                                        'resolution': video_output.get('resolution'),
                                        'frame_rate': video_output.get('frame_rate')
                                    })

                            p['camera_on_periods'] = camera_on_periods
                            p['camera_on_count'] = len(camera_on_periods)

                        all_participants.extend(participants)
                        print(f"[ZoomAPI] QoS Page {page_count + 1}: {len(participants)} participants")

                    next_page_token = data.get('next_page_token', '')
                    page_count += 1

                    if not next_page_token:
                        break

                elif response.status_code == 400:
                    print(f"[ZoomAPI] QoS API: Bad request - {response.text[:200]}")
                    break
                elif response.status_code == 401:
                    print(f"[ZoomAPI] QoS API: Unauthorized - refreshing token")
                    self.access_token = None
                    token = self.get_access_token()
                    headers = {'Authorization': f'Bearer {token}'}
                    continue
                elif response.status_code == 403:
                    print(f"[ZoomAPI] QoS API: Forbidden - requires Business+ plan or dashboard_meetings:read:admin scope")
                    print(f"[ZoomAPI] Response: {response.text[:300]}")
                    break
                elif response.status_code == 404:
                    print(f"[ZoomAPI] QoS API: Meeting not found")
                    break
                else:
                    print(f"[ZoomAPI] QoS API: {response.status_code} - {response.text[:200]}")
                    break

            print(f"[ZoomAPI] QoS: Got {len(all_participants)} participants with camera data")
            return all_participants

        except Exception as e:
            print(f"[ZoomAPI] QoS API error: {e}")
            traceback.print_exc()
            return []

zoom_api = ZoomAPI()


# ==============================================================================
# WEBHOOK EVENT HANDLERS
# ==============================================================================

def is_scout_bot(participant_name, participant_email):
    """Check if participant is the scout bot"""
    if participant_email and SCOUT_BOT_EMAIL:
        if participant_email.lower() == SCOUT_BOT_EMAIL.lower():
            return True
    if participant_name and SCOUT_BOT_NAME:
        if SCOUT_BOT_NAME.lower() in participant_name.lower():
            return True
    return False


def is_calibration_participant(participant_name, participant_email):
    """
    Check if participant is the calibration participant (for "Move Myself" mode).
    Returns True if:
    - Calibration is in progress AND
    - Participant matches the calibration participant OR is Scout Bot
    """
    # If no calibration in progress, only check for scout bot
    if not meeting_state.calibration_in_progress:
        return is_scout_bot(participant_name, participant_email)

    # Check if this is Scout Bot
    if is_scout_bot(participant_name, participant_email):
        return True

    # Check if this is the calibration participant (for "Move Myself" mode)
    if meeting_state.calibration_mode == 'self' and meeting_state.calibration_participant_name:
        cal_name = meeting_state.calibration_participant_name.lower().strip()
        webhook_name = (participant_name or '').lower().strip()

        if not webhook_name:
            return False

        # Check various matching strategies:
        # 1. Exact match
        if webhook_name == cal_name:
            return True
        # 2. Calibration name is substring of webhook name (e.g., "Shashank" in "Shashank Channawar")
        if cal_name in webhook_name:
            return True
        # 3. Webhook name is substring of calibration name (e.g., webhook truncated)
        if webhook_name in cal_name:
            return True
        # 4. First name match (first word matches)
        cal_first = cal_name.split()[0] if cal_name else ''
        webhook_first = webhook_name.split()[0] if webhook_name else ''
        if cal_first and webhook_first and cal_first == webhook_first:
            return True

    return False


def extract_participant_data(data):
    """
    Extract participant data from Zoom webhook with comprehensive fallbacks.
    Zoom webhooks can have different structures depending on event type.
    """
    payload = data.get('payload', {})
    obj = payload.get('object', {})
    participant = obj.get('participant', {})

    # If participant is empty, try alternate locations
    if not participant:
        participant = payload.get('participant', {})

    # Extract with multiple fallback field names
    participant_id = (
        participant.get('user_id') or
        participant.get('id') or
        participant.get('participant_user_id') or
        participant.get('participant_id') or
        obj.get('participant_user_id') or
        str(uuid_lib.uuid4())[:8]  # Last resort: generate temporary ID
    )

    participant_name = (
        participant.get('user_name') or
        participant.get('name') or
        participant.get('participant_name') or
        participant.get('display_name') or
        'Unknown'
    )

    participant_email = (
        participant.get('email') or
        participant.get('user_email') or
        participant.get('participant_email') or
        ''
    )

    meeting_id = str(obj.get('id', '') or obj.get('meeting_id', '') or payload.get('meeting_id', ''))
    meeting_uuid = obj.get('uuid', '') or obj.get('meeting_uuid', '') or payload.get('meeting_uuid', '')
    room_uuid = obj.get('breakout_room_uuid', '') or obj.get('room_uuid', '') or ''

    # Parse timestamp - Zoom sends event_ts in milliseconds
    event_ts = data.get('event_ts', 0)
    if event_ts and event_ts > 0:
        try:
            # Handle both milliseconds and seconds
            if event_ts > 1e12:  # Milliseconds
                event_dt = datetime.fromtimestamp(event_ts / 1000)
            else:  # Seconds
                event_dt = datetime.fromtimestamp(event_ts)
        except (ValueError, OSError):
            event_dt = datetime.utcnow()
    else:
        event_dt = datetime.utcnow()

    return {
        'participant_id': str(participant_id) if participant_id else '',
        'participant_name': str(participant_name) if participant_name else 'Unknown',
        'participant_email': str(participant_email) if participant_email else '',
        'meeting_id': meeting_id,
        'meeting_uuid': meeting_uuid,
        'room_uuid': room_uuid,
        'event_dt': event_dt
    }


def handle_participant_joined(data):
    """Handle participant joined main meeting"""
    # Extract data with comprehensive fallbacks
    p = extract_participant_data(data)

    print(f"[ParticipantJoined] Extracted: id={p['participant_id']}, name={p['participant_name']}, meeting={p['meeting_id']}")

    # Skip scout bot
    if is_scout_bot(p['participant_name'], p['participant_email']):
        print(f"  -> Scout bot joined, skipping event storage")
        return

    # Validate we have required data
    if not p['meeting_id']:
        print(f"  -> ERROR: Missing meeting_id, cannot store event")
        print(f"  -> Raw data: {json.dumps(data, indent=2)[:500]}")
        return

    # Set current meeting
    meeting_state.set_meeting(p['meeting_id'], p['meeting_uuid'])

    event_data = {
        'event_id': str(uuid_lib.uuid4()),
        'event_type': 'participant_joined',
        'event_timestamp': p['event_dt'].isoformat(),
        'event_date': p['event_dt'].strftime('%Y-%m-%d'),
        'meeting_id': p['meeting_id'],
        'meeting_uuid': p['meeting_uuid'],
        'participant_id': p['participant_id'],
        'participant_name': p['participant_name'],
        'participant_email': p['participant_email'],
        'room_uuid': '',
        'room_name': 'Main Room',
        'inserted_at': datetime.utcnow().isoformat()
    }

    # Update participant state
    state = meeting_state.get_participant_state(p['participant_id'])
    state['joined_at'] = p['event_dt'].isoformat()
    state['current_room'] = 'Main Room'

    success = insert_participant_event(event_data)
    print(f"  -> JOIN: {p['participant_name']} {'[OK]' if success else '[FAILED]'}")


def handle_participant_left(data):
    """Handle participant left meeting"""
    p = extract_participant_data(data)

    print(f"[ParticipantLeft] Extracted: id={p['participant_id']}, name={p['participant_name']}")

    # Skip scout bot
    if is_scout_bot(p['participant_name'], p['participant_email']):
        print(f"  -> Scout bot left, skipping")
        return

    if not p['meeting_id']:
        print(f"  -> ERROR: Missing meeting_id")
        return

    event_data = {
        'event_id': str(uuid_lib.uuid4()),
        'event_type': 'participant_left',
        'event_timestamp': p['event_dt'].isoformat(),
        'event_date': p['event_dt'].strftime('%Y-%m-%d'),
        'meeting_id': p['meeting_id'],
        'meeting_uuid': p['meeting_uuid'],
        'participant_id': p['participant_id'],
        'participant_name': p['participant_name'],
        'participant_email': p['participant_email'],
        'room_uuid': '',
        'room_name': '',
        'inserted_at': datetime.utcnow().isoformat()
    }

    success = insert_participant_event(event_data)
    print(f"  -> LEAVE: {p['participant_name']} {'[OK]' if success else '[FAILED]'}")


def handle_breakout_room_join(data):
    """Handle participant joined breakout room"""
    p = extract_participant_data(data)

    print(f"[BreakoutJoin] Extracted: id={p['participant_id']}, name={p['participant_name']}, room={p['room_uuid'][:20] if p['room_uuid'] else 'none'}...")

    if not p['meeting_id']:
        print(f"  -> ERROR: Missing meeting_id")
        return

    # Set current meeting
    meeting_state.set_meeting(p['meeting_id'], p['meeting_uuid'])

    room_uuid = p['room_uuid']

    # If this is calibration participant (Scout Bot or self), learn webhook UUID -> room name mapping
    if is_calibration_participant(p['participant_name'], p['participant_email']):
        cal_mode = meeting_state.calibration_mode
        cal_name = meeting_state.calibration_participant_name or 'Scout Bot'
        print(f"  -> Calibration participant detected: {p['participant_name']} (mode: {cal_mode}, expected: {cal_name})")
        print(f"  -> Calibration in progress: {meeting_state.calibration_in_progress}")
        print(f"  -> Pending room moves: {len(meeting_state.pending_room_moves)}")

        # Scout Bot is moving during calibration
        # Find the oldest unmatched pending room move
        room_name = None
        matched_move = None

        for move in meeting_state.pending_room_moves:
            if not move['matched']:
                room_name = move['room_name']
                matched_move = move
                break

        # Fallback to scout_bot_current_room if no pending moves
        if not room_name and hasattr(meeting_state, 'scout_bot_current_room'):
            room_name = meeting_state.scout_bot_current_room

        if room_name and room_uuid:
            # Mark the move as matched
            if matched_move:
                matched_move['matched'] = True
                matched_move['webhook_uuid'] = room_uuid
                print(f"  -> MATCHED pending move: {room_name}")

            # Store webhook UUID -> room name mapping in memory
            meeting_state.add_webhook_room_mapping(room_uuid, room_name)
            print(f"  -> CALIBRATION: Learned webhook UUID {room_uuid[:20]}... = {room_name}")

            # Also store in BigQuery for persistence
            try:
                today = datetime.utcnow().strftime('%Y-%m-%d')
                mapping_row = {
                    'mapping_id': str(uuid_lib.uuid4()),
                    'meeting_id': str(p['meeting_id']),
                    'meeting_uuid': p['meeting_uuid'] or '',
                    'room_uuid': room_uuid,
                    'room_name': room_name,
                    'room_index': len([m for m in meeting_state.pending_room_moves if m['matched']]),
                    'mapping_date': today,
                    'mapped_at': datetime.utcnow().isoformat(),
                    'source': 'webhook_calibration'
                }
                success = insert_room_mappings([mapping_row])
                if success:
                    print(f"  -> SAVED webhook mapping to BigQuery!")
                else:
                    print(f"  -> WARNING: BigQuery insert returned false")
            except Exception as e:
                print(f"  -> WARNING: Could not save webhook mapping: {e}")
                traceback.print_exc()
        else:
            print(f"  -> WARNING: Could not match webhook UUID - room_name={room_name}, room_uuid={room_uuid[:20] if room_uuid else 'None'}")

        print(f"  -> Calibration participant in breakout room, skipping event storage")
        return

    # Get room name from mapping
    if room_uuid:
        room_name = meeting_state.get_room_name(room_uuid) or f'Room-{room_uuid[:8]}'
    else:
        room_name = 'Unknown Room'
        print(f"  -> WARNING: No room_uuid in event data")

    event_data = {
        'event_id': str(uuid_lib.uuid4()),
        'event_type': 'breakout_room_joined',
        'event_timestamp': p['event_dt'].isoformat(),
        'event_date': p['event_dt'].strftime('%Y-%m-%d'),
        'meeting_id': p['meeting_id'],
        'meeting_uuid': p['meeting_uuid'],
        'participant_id': p['participant_id'],
        'participant_name': p['participant_name'],
        'participant_email': p['participant_email'],
        'room_uuid': room_uuid,
        'room_name': room_name,
        'inserted_at': datetime.utcnow().isoformat()
    }

    # Update participant state
    state = meeting_state.get_participant_state(p['participant_id'])
    state['current_room'] = room_name

    success = insert_participant_event(event_data)
    print(f"  -> ROOM JOIN: {p['participant_name']} -> {room_name} {'[OK]' if success else '[FAILED]'}")


def handle_breakout_room_leave(data):
    """Handle participant left breakout room"""
    p = extract_participant_data(data)

    print(f"[BreakoutLeave] Extracted: id={p['participant_id']}, name={p['participant_name']}")

    # Skip calibration participant (Scout Bot or self)
    if is_calibration_participant(p['participant_name'], p['participant_email']):
        print(f"  -> Calibration participant left breakout room, skipping")
        return

    if not p['meeting_id']:
        print(f"  -> ERROR: Missing meeting_id")
        return

    room_uuid = p['room_uuid']
    room_name = meeting_state.get_room_name(room_uuid) if room_uuid else 'Unknown Room'
    if not room_name and room_uuid:
        room_name = f'Room-{room_uuid[:8]}'

    event_data = {
        'event_id': str(uuid_lib.uuid4()),
        'event_type': 'breakout_room_left',
        'event_timestamp': p['event_dt'].isoformat(),
        'event_date': p['event_dt'].strftime('%Y-%m-%d'),
        'meeting_id': p['meeting_id'],
        'meeting_uuid': p['meeting_uuid'],
        'participant_id': p['participant_id'],
        'participant_name': p['participant_name'],
        'participant_email': p['participant_email'],
        'room_uuid': room_uuid,
        'room_name': room_name,
        'inserted_at': datetime.utcnow().isoformat()
    }

    success = insert_participant_event(event_data)
    print(f"  -> ROOM LEAVE: {p['participant_name']} <- {room_name} {'[OK]' if success else '[FAILED]'}")


def handle_camera_event(data, camera_on):
    """Handle camera on/off event"""
    p = extract_participant_data(data)

    print(f"[CameraEvent] Extracted: id={p['participant_id']}, name={p['participant_name']}, on={camera_on}")

    # Skip scout bot
    if is_scout_bot(p['participant_name'], p['participant_email']):
        print(f"  -> Scout bot camera event, skipping")
        return

    if not p['meeting_id']:
        print(f"  -> ERROR: Missing meeting_id")
        return

    event_dt = p['event_dt']

    # Get current room for participant
    state = meeting_state.get_participant_state(p['participant_id'])
    current_room = state.get('current_room', 'Main Room') or 'Main Room'

    # Calculate duration if camera turning OFF
    duration_seconds = None
    if not camera_on and state.get('camera_on_since'):
        try:
            on_time = datetime.fromisoformat(state['camera_on_since'])
            duration_seconds = int((event_dt - on_time).total_seconds())
            # Sanity check - duration should be positive and reasonable
            if duration_seconds < 0:
                duration_seconds = 0
            elif duration_seconds > 86400:  # More than 24 hours
                duration_seconds = None  # Discard unreasonable value
        except Exception as e:
            print(f"  -> ERROR calculating duration: {e}")
            duration_seconds = None

    camera_event = {
        'event_id': str(uuid_lib.uuid4()),
        'event_type': 'camera_on' if camera_on else 'camera_off',
        'event_timestamp': event_dt.isoformat(),
        'event_date': event_dt.strftime('%Y-%m-%d'),
        'event_time': event_dt.strftime('%H:%M:%S'),
        'meeting_id': p['meeting_id'],
        'meeting_uuid': p['meeting_uuid'],
        'participant_id': p['participant_id'],
        'participant_name': p['participant_name'],
        'participant_email': p['participant_email'],
        'camera_on': camera_on,
        'room_name': current_room,
        'duration_seconds': duration_seconds,
        'inserted_at': datetime.utcnow().isoformat()
    }

    # Update state BEFORE insert so we track camera_on_since correctly
    meeting_state.update_camera_state(p['participant_id'], camera_on, event_dt.isoformat())

    success = insert_camera_event(camera_event)
    status = 'ON' if camera_on else 'OFF'
    duration_str = f" (was on for {duration_seconds}s)" if duration_seconds is not None else ""
    print(f"  -> CAMERA {status}: {p['participant_name']} at {event_dt.strftime('%H:%M:%S')}{duration_str} {'[OK]' if success else '[FAILED]'}")


def safe_int(value, default=0):
    """Safely convert value to int, handling None and empty strings"""
    if value is None or value == '':
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_str(value, default=''):
    """Safely convert value to string, handling None"""
    if value is None:
        return default
    return str(value).strip() if value else default


def handle_meeting_ended(data):
    """Handle meeting ended - collect final QoS data"""
    payload = data.get('payload', {})
    obj = payload.get('object', {})
    meeting_uuid = obj.get('uuid', '')
    meeting_id = str(obj.get('id', ''))

    print(f"[Meeting] Meeting ended: {meeting_uuid}")
    print(f"[Meeting] Meeting ID: {meeting_id}")

    # Collect QoS data in background
    def collect_qos():
        time.sleep(30)  # Wait for Zoom to finalize data
        collected_count = 0
        error_count = 0

        try:
            # Try multiple methods to get participant data
            participants = zoom_api.get_past_meeting_participants(meeting_uuid)

            if not participants:
                print(f"[QoS] No participants found via past_meeting API")
                # Try with meeting_id instead
                participants = zoom_api.get_past_meeting_participants(meeting_id)

            if not participants:
                print(f"[QoS] No participants found - API may require Business+ plan")
                return

            print(f"[QoS] Processing {len(participants)} participants...")
            print(f"[QoS] Sample raw data: {json.dumps(participants[0] if participants else {}, indent=2)}")

            for p in participants:
                try:
                    # Extract participant ID with fallbacks
                    participant_id = safe_str(
                        p.get('user_id') or p.get('id') or p.get('participant_user_id') or p.get('registrant_id'),
                        default='unknown'
                    )

                    # Extract name with fallbacks
                    participant_name = safe_str(
                        p.get('name') or p.get('user_name') or p.get('participant_name'),
                        default='Unknown'
                    )

                    # Extract email with fallbacks
                    participant_email = safe_str(
                        p.get('user_email') or p.get('email') or p.get('participant_email'),
                        default=''
                    )

                    # Zoom API returns 'duration' in SECONDS - convert to minutes
                    duration_seconds = safe_int(p.get('duration', 0))
                    duration_minutes = duration_seconds // 60 if duration_seconds > 0 else 0

                    # Extract times - handle various date formats
                    join_time = safe_str(p.get('join_time', ''))
                    leave_time = safe_str(p.get('leave_time', ''))

                    # Attentiveness score - may be string or number
                    attentiveness = p.get('attentiveness_score')
                    if attentiveness is None:
                        attentiveness_score = ''
                    elif isinstance(attentiveness, (int, float)):
                        attentiveness_score = str(attentiveness)
                    else:
                        attentiveness_score = safe_str(attentiveness)

                    qos_data = {
                        'qos_id': str(uuid_lib.uuid4()),
                        'meeting_uuid': safe_str(meeting_uuid),
                        'participant_id': participant_id,
                        'participant_name': participant_name,
                        'participant_email': participant_email,
                        'join_time': join_time,
                        'leave_time': leave_time,
                        'duration_minutes': duration_minutes,
                        'attentiveness_score': attentiveness_score,
                        'recorded_at': datetime.utcnow().isoformat(),
                        'event_date': datetime.utcnow().strftime('%Y-%m-%d')
                    }

                    # Log each insert for debugging
                    print(f"[QoS] Inserting: {participant_name} - duration={duration_minutes}min (raw={duration_seconds}s)")

                    if insert_qos_data(qos_data):
                        collected_count += 1
                    else:
                        error_count += 1
                        print(f"[QoS] Failed to insert data for {participant_name}")

                except Exception as pe:
                    error_count += 1
                    print(f"[QoS] Error processing participant: {pe}")
                    print(f"[QoS] Raw participant data: {json.dumps(p, indent=2)}")

            print(f"[QoS] Collection complete: {collected_count} success, {error_count} errors")

        except Exception as e:
            print(f"[QoS] Collection error: {e}")
            traceback.print_exc()

    thread = threading.Thread(target=collect_qos, daemon=True)
    thread.start()


# ==============================================================================
# FLASK ROUTES
# ==============================================================================

@app.route('/')
@app.route('/health')
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'Breakout Room Calibrator',
        'version': '2.0.0',
        'config': {
            'project': GCP_PROJECT_ID,
            'dataset': BQ_DATASET,
            'scout_bot': SCOUT_BOT_NAME
        },
        'current_meeting': {
            'meeting_id': meeting_state.meeting_id,
            'calibration_complete': meeting_state.calibration_complete,
            'rooms_mapped': len(meeting_state.uuid_to_name)
        },
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    """Main Zoom webhook endpoint"""
    if request.method == 'GET':
        return jsonify({'status': 'Webhook ready'})

    # Get raw data for logging
    try:
        data = request.json
    except Exception as e:
        print(f"[Webhook] ERROR: Failed to parse JSON: {e}")
        print(f"[Webhook] Raw body: {request.data[:500] if request.data else 'empty'}")
        return jsonify({'error': 'Invalid JSON'}), 400

    if not data:
        print(f"[Webhook] ERROR: Empty request body")
        return jsonify({'error': 'Empty body'}), 400

    event = data.get('event', '')

    print(f"\n{'='*60}")
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] WEBHOOK EVENT: {event}")
    print(f"{'='*60}")

    # Log raw payload for debugging (first 500 chars)
    raw_str = json.dumps(data)
    if len(raw_str) > 500:
        print(f"[Webhook] Payload (truncated): {raw_str[:500]}...")
    else:
        print(f"[Webhook] Payload: {raw_str}")

    # Handle URL validation
    if event == 'endpoint.url_validation':
        plain_token = data.get('payload', {}).get('plainToken', '')
        encrypted_token = hmac.new(
            key=ZOOM_WEBHOOK_SECRET.encode('utf-8'),
            msg=plain_token.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()
        print(f"[Webhook] URL validation successful")
        return jsonify({
            'plainToken': plain_token,
            'encryptedToken': encrypted_token
        })

    # Route events to handlers with error catching
    try:
        if event == 'meeting.participant_joined':
            handle_participant_joined(data)

        elif event == 'meeting.participant_left':
            handle_participant_left(data)

        elif event == 'meeting.participant_joined_breakout_room':
            handle_breakout_room_join(data)

        elif event == 'meeting.participant_left_breakout_room':
            handle_breakout_room_leave(data)

        elif event in ['meeting.participant_video_on', 'meeting.participant_video_started']:
            handle_camera_event(data, camera_on=True)

        elif event in ['meeting.participant_video_off', 'meeting.participant_video_stopped']:
            handle_camera_event(data, camera_on=False)

        elif event == 'meeting.ended':
            handle_meeting_ended(data)

        else:
            print(f"[Webhook] Unhandled event type: {event}")

    except Exception as e:
        print(f"[Webhook] ERROR handling {event}: {e}")
        import traceback
        traceback.print_exc()
        # Still return success to Zoom so it doesn't retry
        return jsonify({'status': 'error logged', 'event': event}), 200

    return jsonify({'status': 'success'})


# ==============================================================================
# CALIBRATION ENDPOINTS (For Zoom SDK App)
# ==============================================================================

@app.route('/calibration/start', methods=['POST'])
def calibration_start():
    """Start calibration session"""
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    meeting_uuid = data.get('meeting_uuid')

    # Calibration participant info (for "Move Myself" mode)
    calibration_mode = data.get('calibration_mode', 'scout_bot')
    calibration_participant_name = data.get('calibration_participant_name', '')
    calibration_participant_uuid = data.get('calibration_participant_uuid', '')

    if not meeting_id:
        return jsonify({'error': 'meeting_id required'}), 400

    # Reset state for new calibration
    meeting_state.set_meeting(meeting_id, meeting_uuid)
    meeting_state.calibration_complete = False
    meeting_state.calibration_in_progress = True
    meeting_state.pending_room_moves = []

    # Store calibration participant info
    meeting_state.calibration_mode = calibration_mode
    meeting_state.calibration_participant_name = calibration_participant_name
    meeting_state.calibration_participant_uuid = calibration_participant_uuid

    print(f"\n{'='*50}")
    print(f"[Calibration] STARTED for meeting {meeting_id}")
    print(f"[Calibration] Mode: {calibration_mode}")
    if calibration_mode == 'self':
        print(f"[Calibration] Participant: {calibration_participant_name}")
    else:
        print(f"[Calibration] Using Scout Bot: {SCOUT_BOT_NAME}")
    print(f"[Calibration] Webhook UUID capture ENABLED")
    print(f"{'='*50}\n")

    return jsonify({
        'success': True,
        'message': 'Calibration started',
        'meeting_id': meeting_id,
        'calibration_mode': calibration_mode,
        'calibration_participant': calibration_participant_name or SCOUT_BOT_NAME
    })


@app.route('/calibration/mapping', methods=['POST'])
def calibration_mapping():
    """Receive room mappings from Zoom SDK App"""
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    meeting_uuid = data.get('meeting_uuid')
    room_mapping = data.get('room_mapping', [])

    if not meeting_id or not room_mapping:
        return jsonify({'error': 'meeting_id and room_mapping required'}), 400

    # Update meeting state
    meeting_state.set_meeting(meeting_id, meeting_uuid)

    # Store mappings in memory and track pending room moves for webhook UUID learning
    for room in room_mapping:
        room_uuid = room.get('room_uuid', '')
        room_name = room.get('room_name', '')
        if room_uuid and room_name:
            meeting_state.add_room_mapping(room_uuid, room_name)
            # Track the current room Scout Bot is moving to
            meeting_state.scout_bot_current_room = room_name
            # Add to pending moves queue with timestamp (for matching webhooks)
            move_time = datetime.utcnow()
            meeting_state.pending_room_moves.append({
                'room_name': room_name,
                'sdk_uuid': room_uuid,
                'timestamp': move_time,
                'matched': False
            })
            print(f"[Calibration] Scout Bot moving to: {room_name} (pending webhook match)")

    # Store in BigQuery
    today = datetime.utcnow().strftime('%Y-%m-%d')
    bq_rows = [{
        'mapping_id': str(uuid_lib.uuid4()),
        'meeting_id': str(meeting_id),
        'meeting_uuid': meeting_uuid or '',
        'room_uuid': room.get('room_uuid', ''),
        'room_name': room.get('room_name', ''),
        'room_index': room.get('room_index', 0),
        'mapping_date': today,
        'mapped_at': datetime.utcnow().isoformat(),
        'source': 'zoom_sdk_app'
    } for room in room_mapping if room.get('room_uuid') and room.get('room_name')]

    if bq_rows:
        insert_room_mappings(bq_rows)

    print(f"[Calibration] Received {len(room_mapping)} room mappings, {len(meeting_state.pending_room_moves)} pending webhook matches")
    for room in room_mapping[:5]:
        print(f"  - {room.get('room_name')} = {room.get('room_uuid', '')[:20]}...")
    if len(room_mapping) > 5:
        print(f"  ... and {len(room_mapping) - 5} more")

    return jsonify({
        'success': True,
        'mappings_received': len(room_mapping),
        'total_stored': len(meeting_state.uuid_to_name),
        'pending_webhook_matches': len([m for m in meeting_state.pending_room_moves if not m['matched']])
    })


@app.route('/calibration/complete', methods=['POST'])
def calibration_complete():
    """Mark calibration as complete"""
    data = request.json or {}
    meeting_id = data.get('meeting_id')
    success = data.get('success', True)
    total_rooms = data.get('total_rooms', 0)
    mapped_rooms = data.get('mapped_rooms', 0)

    meeting_state.calibration_complete = success
    meeting_state.calibrated_at = datetime.utcnow().isoformat()
    meeting_state.calibration_in_progress = False

    # Count webhook UUID matches
    webhook_matches = len([m for m in meeting_state.pending_room_moves if m.get('matched')])
    unmatched = len([m for m in meeting_state.pending_room_moves if not m.get('matched')])

    print(f"\n{'='*50}")
    print(f"[Calibration] COMPLETE - {mapped_rooms}/{total_rooms} SDK room mappings")
    print(f"[Calibration] Webhook UUID matches: {webhook_matches} matched, {unmatched} unmatched")
    print(f"[Calibration] Total mappings in memory: {len(meeting_state.uuid_to_name)}")
    print(f"[Calibration] Scout Bot can now leave the meeting")
    print(f"{'='*50}\n")

    return jsonify({
        'success': True,
        'message': 'Calibration complete - Scout Bot can leave now',
        'sdk_mappings': mapped_rooms,
        'webhook_uuid_matches': webhook_matches,
        'unmatched_rooms': unmatched
    })


@app.route('/calibration/status', methods=['GET'])
def calibration_status():
    """Get current calibration status"""
    return jsonify({
        'meeting_id': meeting_state.meeting_id,
        'calibration_complete': meeting_state.calibration_complete,
        'calibrated_at': meeting_state.calibrated_at,
        'rooms_mapped': len(meeting_state.uuid_to_name),
        'room_names': list(meeting_state.name_to_uuid.keys())[:20]
    })


@app.route('/mappings', methods=['GET'])
def get_mappings():
    """Get current room mappings"""
    return jsonify({
        'meeting_id': meeting_state.meeting_id,
        'calibration_complete': meeting_state.calibration_complete,
        'mappings': [
            {'room_name': name, 'room_uuid': uuid}
            for name, uuid in meeting_state.name_to_uuid.items()
        ],
        'total': len(meeting_state.name_to_uuid)
    })


# ==============================================================================
# REPORT ENDPOINTS
# ==============================================================================

@app.route('/report/generate', methods=['POST'])
def generate_report():
    """Manually trigger report generation"""
    data = request.json or {}
    report_date = data.get('date', datetime.utcnow().strftime('%Y-%m-%d'))

    try:
        from report_generator import generate_daily_report, send_report_email

        report = generate_daily_report(report_date)

        if SENDGRID_API_KEY and REPORT_EMAIL_TO:
            send_report_email(report, report_date)
            return jsonify({
                'success': True,
                'message': f'Report generated and sent to {REPORT_EMAIL_TO}',
                'date': report_date
            })
        else:
            return jsonify({
                'success': True,
                'message': 'Report generated (email not configured)',
                'date': report_date,
                'report': report
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/report/preview/<date>', methods=['GET'])
def preview_report(date):
    """Preview report data for a date"""
    try:
        from report_generator import generate_daily_report
        report = generate_daily_report(date)
        return jsonify(report)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==============================================================================
# MANUAL QOS COLLECTION
# ==============================================================================

@app.route('/qos/collect', methods=['POST'])
def collect_qos_manual():
    """
    Manually collect QoS data for a meeting.
    Use this when meeting.ended webhook is not received.

    POST /qos/collect
    Body: {"meeting_uuid": "xxx"} or {"meeting_id": "123456"}
    """
    data = request.json or {}
    meeting_uuid = data.get('meeting_uuid', '')
    meeting_id = data.get('meeting_id', '')

    if not meeting_uuid and not meeting_id:
        return jsonify({'error': 'meeting_uuid or meeting_id required'}), 400

    print(f"[QoS] Manual collection triggered")
    print(f"[QoS] Meeting UUID: {meeting_uuid}")
    print(f"[QoS] Meeting ID: {meeting_id}")

    collected_count = 0
    error_count = 0
    participants_data = []

    try:
        # Try with meeting_uuid first
        participants = []
        if meeting_uuid:
            participants = zoom_api.get_past_meeting_participants(meeting_uuid)

        # Fallback to meeting_id
        if not participants and meeting_id:
            participants = zoom_api.get_past_meeting_participants(meeting_id)

        if not participants:
            return jsonify({
                'success': False,
                'error': 'No participants found - meeting may still be in progress or API requires Business+ plan',
                'meeting_uuid': meeting_uuid,
                'meeting_id': meeting_id
            }), 404

        print(f"[QoS] Found {len(participants)} participants")

        for p in participants:
            try:
                participant_id = safe_str(
                    p.get('user_id') or p.get('id') or p.get('participant_user_id'),
                    default='unknown'
                )
                participant_name = safe_str(
                    p.get('name') or p.get('user_name'),
                    default='Unknown'
                )
                participant_email = safe_str(
                    p.get('user_email') or p.get('email'),
                    default=''
                )

                # Duration in seconds from API, convert to minutes
                duration_seconds = safe_int(p.get('duration', 0))
                duration_minutes = duration_seconds // 60 if duration_seconds > 0 else 0

                join_time = safe_str(p.get('join_time', ''))
                leave_time = safe_str(p.get('leave_time', ''))

                qos_data = {
                    'qos_id': str(uuid_lib.uuid4()),
                    'meeting_uuid': safe_str(meeting_uuid or meeting_id),
                    'participant_id': participant_id,
                    'participant_name': participant_name,
                    'participant_email': participant_email,
                    'join_time': join_time,
                    'leave_time': leave_time,
                    'duration_minutes': duration_minutes,
                    'attentiveness_score': safe_str(p.get('attentiveness_score', '')),
                    'recorded_at': datetime.utcnow().isoformat(),
                    'event_date': datetime.utcnow().strftime('%Y-%m-%d')
                }

                if insert_qos_data(qos_data):
                    collected_count += 1
                    participants_data.append({
                        'name': participant_name,
                        'email': participant_email,
                        'duration_minutes': duration_minutes
                    })
                else:
                    error_count += 1

            except Exception as pe:
                error_count += 1
                print(f"[QoS] Error processing participant: {pe}")

        return jsonify({
            'success': True,
            'collected': collected_count,
            'errors': error_count,
            'participants': participants_data[:20],  # First 20 for preview
            'total_participants': len(participants)
        })

    except Exception as e:
        print(f"[QoS] Manual collection error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/qos/status', methods=['GET'])
def qos_status():
    """Check QoS data status for recent dates"""
    try:
        client = get_bq_client()
        query = f"""
        SELECT
            event_date,
            COUNT(*) as records,
            COUNT(DISTINCT participant_name) as participants
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_QOS_TABLE}`
        WHERE event_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY event_date
        ORDER BY event_date DESC
        """
        results = list(client.query(query).result())

        return jsonify({
            'success': True,
            'qos_data': [dict(row.items()) for row in results]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/qos/scheduled', methods=['POST'])
def qos_scheduled_collection():
    """
    Scheduled QoS collection - called by Cloud Scheduler.
    Finds yesterday's meeting UUID from BigQuery and collects QoS data.

    Can also be called with a specific date:
    POST /qos/scheduled
    Body: {"date": "2026-02-18"} (optional, defaults to yesterday)
    """
    data = request.json or {}
    target_date = data.get('date')

    if not target_date:
        # Default to yesterday
        target_date = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"[ScheduledQoS] Starting collection for date: {target_date}")

    try:
        client = get_bq_client()

        # Find meeting UUID(s) from participant_events for that date
        query = f"""
        SELECT DISTINCT meeting_uuid
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_EVENTS_TABLE}`
        WHERE event_date = '{target_date}'
          AND meeting_uuid IS NOT NULL
          AND meeting_uuid != ''
        LIMIT 5
        """
        results = list(client.query(query).result())

        if not results:
            return jsonify({
                'success': False,
                'error': f'No meetings found for date {target_date}',
                'date': target_date
            }), 404

        meeting_uuids = [row.meeting_uuid for row in results]
        print(f"[ScheduledQoS] Found {len(meeting_uuids)} meeting(s): {meeting_uuids}")

        # Check if QoS already collected for this date
        check_query = f"""
        SELECT COUNT(*) as count
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_QOS_TABLE}`
        WHERE event_date = '{target_date}'
        """
        check_result = list(client.query(check_query).result())[0]
        existing_count = check_result.count

        if existing_count > 50:
            print(f"[ScheduledQoS] QoS already collected: {existing_count} records")
            return jsonify({
                'success': True,
                'message': f'QoS already collected for {target_date}',
                'existing_records': existing_count,
                'date': target_date
            })

        # Collect QoS for each meeting
        total_collected = 0
        total_errors = 0
        results_detail = []

        for meeting_uuid in meeting_uuids:
            print(f"[ScheduledQoS] Collecting for meeting: {meeting_uuid}")

            try:
                participants = zoom_api.get_past_meeting_participants(meeting_uuid)

                if not participants:
                    results_detail.append({
                        'meeting_uuid': meeting_uuid,
                        'status': 'no_participants'
                    })
                    continue

                collected = 0
                errors = 0

                for p in participants:
                    try:
                        participant_id = safe_str(
                            p.get('user_id') or p.get('id') or p.get('participant_user_id'),
                            default='unknown'
                        )
                        participant_name = safe_str(
                            p.get('name') or p.get('user_name'),
                            default='Unknown'
                        )
                        participant_email = safe_str(
                            p.get('user_email') or p.get('email'),
                            default=''
                        )
                        duration_seconds = safe_int(p.get('duration', 0))
                        duration_minutes = duration_seconds // 60 if duration_seconds > 0 else 0

                        qos_data = {
                            'qos_id': str(uuid_lib.uuid4()),
                            'meeting_uuid': safe_str(meeting_uuid),
                            'participant_id': participant_id,
                            'participant_name': participant_name,
                            'participant_email': participant_email,
                            'join_time': safe_str(p.get('join_time', '')),
                            'leave_time': safe_str(p.get('leave_time', '')),
                            'duration_minutes': duration_minutes,
                            'attentiveness_score': str(p.get('attentiveness_score', '')),
                            'recorded_at': datetime.utcnow().isoformat(),
                            'event_date': target_date  # Use target date, not today
                        }

                        if insert_qos_data(qos_data):
                            collected += 1
                        else:
                            errors += 1

                    except Exception as pe:
                        errors += 1
                        print(f"[ScheduledQoS] Error: {pe}")

                total_collected += collected
                total_errors += errors
                results_detail.append({
                    'meeting_uuid': meeting_uuid,
                    'collected': collected,
                    'errors': errors
                })

            except Exception as me:
                print(f"[ScheduledQoS] Meeting error: {me}")
                results_detail.append({
                    'meeting_uuid': meeting_uuid,
                    'status': 'error',
                    'error': str(me)
                })

        print(f"[ScheduledQoS] Complete: {total_collected} collected, {total_errors} errors")

        # Cleanup old QoS data (older than 2 days)
        cleanup_deleted = 0
        try:
            cleanup_date = (datetime.utcnow() - timedelta(days=2)).strftime('%Y-%m-%d')
            cleanup_query = f"""
            DELETE FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_QOS_TABLE}`
            WHERE event_date < '{cleanup_date}'
            """
            cleanup_job = client.query(cleanup_query)
            cleanup_job.result()
            cleanup_deleted = cleanup_job.num_dml_affected_rows or 0
            print(f"[ScheduledQoS] Cleanup: Deleted {cleanup_deleted} old QoS records (before {cleanup_date})")
        except Exception as ce:
            print(f"[ScheduledQoS] Cleanup error (non-fatal): {ce}")

        return jsonify({
            'success': True,
            'date': target_date,
            'meetings_processed': len(meeting_uuids),
            'total_collected': total_collected,
            'total_errors': total_errors,
            'cleanup_deleted': cleanup_deleted,
            'details': results_detail
        })

    except Exception as e:
        print(f"[ScheduledQoS] Error: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ==============================================================================
# ZOOM SDK APP (STATIC FILES)
# ==============================================================================

# Zoom App OAuth credentials (User-managed app)
ZOOM_APP_CLIENT_ID = os.environ.get('ZOOM_APP_CLIENT_ID', 'raEkn6HpTkWO_DCO3z5zGA')
ZOOM_APP_CLIENT_SECRET = os.environ.get('ZOOM_APP_CLIENT_SECRET', '')

@app.route('/app')
@app.route('/app/')
def serve_zoom_app():
    """Serve Zoom SDK app - handle OAuth callback if code present"""
    # Check if this is an OAuth callback with authorization code
    code = request.args.get('code')
    if code:
        print(f"[OAuth] Received authorization code: {code[:20]}...")
        # For Zoom Apps SDK, we don't need to exchange the code here
        # The SDK handles authentication internally
        # Just serve the app and let SDK initialize

    # Serve the React app
    return send_from_directory(REACT_BUILD_PATH, 'index.html')


@app.route('/app/<path:path>', methods=['GET', 'POST'])
def serve_zoom_app_static(path):
    """Serve Zoom SDK app static files or forward API calls"""
    # Forward API calls to actual endpoints
    if path.startswith('calibration/'):
        if request.method == 'POST':
            # Forward to calibration endpoints
            if path == 'calibration/start':
                return calibration_start()
            elif path == 'calibration/mapping':
                return calibration_mapping()
            elif path == 'calibration/complete':
                return calibration_complete()
        elif request.method == 'GET':
            if path == 'calibration/status':
                return calibration_status()

    # Serve static files
    return send_from_directory(REACT_BUILD_PATH, path)


# ==============================================================================
# DEBUG ENDPOINTS
# ==============================================================================

@app.route('/debug/state', methods=['GET'])
def debug_state():
    """Debug current state"""
    return jsonify({
        'meeting': {
            'id': meeting_state.meeting_id,
            'uuid': meeting_state.meeting_uuid,
            'date': meeting_state.meeting_date,
            'calibration_complete': meeting_state.calibration_complete
        },
        'rooms_mapped': len(meeting_state.uuid_to_name),
        'participants_tracked': len(meeting_state.participant_states),
        'participant_states': {
            k: v for k, v in list(meeting_state.participant_states.items())[:10]
        }
    })


@app.route('/debug/reset', methods=['POST'])
def debug_reset():
    """Reset meeting state (for testing)"""
    meeting_state.reset()
    return jsonify({'status': 'reset', 'message': 'State cleared'})


@app.route('/test/bigquery', methods=['GET'])
def test_bigquery():
    """Test BigQuery connection and show config"""
    results = {
        'config': {
            'project_id': GCP_PROJECT_ID,
            'dataset': BQ_DATASET,
            'events_table': BQ_EVENTS_TABLE,
            'camera_table': BQ_CAMERA_TABLE,
            'qos_table': BQ_QOS_TABLE,
            'mappings_table': BQ_MAPPINGS_TABLE
        },
        'tables': {}
    }

    if not GCP_PROJECT_ID:
        results['error'] = 'GCP_PROJECT_ID not configured!'
        return jsonify(results), 500

    try:
        client = get_bq_client()

        # Test each table - use partition filter for tables that require it
        today = datetime.utcnow().strftime('%Y-%m-%d')

        for table_name, table_var in [
            ('participant_events', BQ_EVENTS_TABLE),
            ('camera_events', BQ_CAMERA_TABLE),
            ('qos_data', BQ_QOS_TABLE),
            ('room_mappings', BQ_MAPPINGS_TABLE)
        ]:
            try:
                # camera_events requires partition filter
                if table_var == BQ_CAMERA_TABLE:
                    query = f"SELECT COUNT(*) as count FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{table_var}` WHERE event_date = '{today}'"
                else:
                    query = f"SELECT COUNT(*) as count FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.{table_var}`"
                result = list(client.query(query).result())
                count = result[0]['count'] if result else 0
                results['tables'][table_name] = {'status': 'OK', 'count': count}
            except Exception as te:
                results['tables'][table_name] = {'status': 'ERROR', 'error': str(te)}

        results['status'] = 'BigQuery OK'
        return jsonify(results)

    except Exception as e:
        results['status'] = 'ERROR'
        results['error'] = str(e)
        return jsonify(results), 500


@app.route('/test/webhook-insert', methods=['POST'])
def test_webhook_insert():
    """
    Test endpoint to simulate a webhook and verify BigQuery insert.
    POST with optional JSON body to test with custom data.
    """
    test_data = request.json or {}

    # Create test event
    test_event = {
        'event_id': str(uuid_lib.uuid4()),
        'event_type': test_data.get('event_type', 'test_event'),
        'event_timestamp': datetime.utcnow().isoformat(),
        'event_date': datetime.utcnow().strftime('%Y-%m-%d'),
        'meeting_id': test_data.get('meeting_id', 'test_meeting_123'),
        'meeting_uuid': test_data.get('meeting_uuid', 'test_uuid_123'),
        'participant_id': test_data.get('participant_id', 'test_participant'),
        'participant_name': test_data.get('participant_name', 'Test User'),
        'participant_email': test_data.get('participant_email', 'test@example.com'),
        'room_uuid': test_data.get('room_uuid', ''),
        'room_name': test_data.get('room_name', 'Test Room'),
        'inserted_at': datetime.utcnow().isoformat()
    }

    print(f"[TEST] Inserting test event: {json.dumps(test_event, indent=2)}")

    success = insert_participant_event(test_event)

    return jsonify({
        'test_event': test_event,
        'insert_success': success,
        'config': {
            'project_id': GCP_PROJECT_ID,
            'dataset': BQ_DATASET,
            'table': BQ_EVENTS_TABLE
        }
    }), 200 if success else 500


@app.route('/test/qos-insert', methods=['POST'])
def test_qos_insert():
    """Test QoS data insert with sample data"""
    test_data = request.json or {}

    qos_event = {
        'qos_id': str(uuid_lib.uuid4()),
        'meeting_uuid': test_data.get('meeting_uuid', 'test_meeting_uuid'),
        'participant_id': test_data.get('participant_id', 'test_participant'),
        'participant_name': test_data.get('participant_name', 'Test User'),
        'participant_email': test_data.get('participant_email', 'test@example.com'),
        'join_time': test_data.get('join_time', datetime.utcnow().isoformat()),
        'leave_time': test_data.get('leave_time', datetime.utcnow().isoformat()),
        'duration_minutes': test_data.get('duration_minutes', 45),
        'attentiveness_score': test_data.get('attentiveness_score', '95'),
        'recorded_at': datetime.utcnow().isoformat(),
        'event_date': datetime.utcnow().strftime('%Y-%m-%d')
    }

    print(f"[TEST] Inserting test QoS: {json.dumps(qos_event, indent=2)}")

    success = insert_qos_data(qos_event)

    return jsonify({
        'qos_event': qos_event,
        'insert_success': success,
        'config': {
            'project_id': GCP_PROJECT_ID,
            'dataset': BQ_DATASET,
            'table': BQ_QOS_TABLE
        }
    }), 200 if success else 500


@app.route('/test/camera-qos', methods=['GET', 'POST'])
def test_camera_qos():
    """
    Test Dashboard QoS API to get camera status via video_output stats.

    GET: Use current meeting ID
    POST: {"meeting_id": "123456"} to specify meeting

    Requires: Business+ plan and dashboard_meetings:read:admin scope
    """
    data = request.json or {}
    meeting_id = data.get('meeting_id') or meeting_state.meeting_id

    if not meeting_id:
        return jsonify({
            'success': False,
            'error': 'No meeting_id provided and no active meeting',
            'hint': 'POST with {"meeting_id": "your_meeting_id"}'
        }), 400

    print(f"[TestCameraQoS] Fetching camera data for meeting: {meeting_id}")

    try:
        # Optional search parameter
        search_name = data.get('search', '').lower()

        participants = zoom_api.get_meeting_participants_qos(meeting_id)

        if not participants:
            return jsonify({
                'success': False,
                'error': 'No QoS data returned - may require Business+ plan or dashboard_meetings:read:admin scope',
                'meeting_id': meeting_id
            }), 404

        # Format results
        camera_data = []
        for p in participants:
            camera_data.append({
                'user_id': p.get('user_id'),
                'user_name': p.get('user_name'),
                'email': p.get('email', ''),
                'join_time': p.get('join_time'),
                'leave_time': p.get('leave_time'),
                'camera_on_periods': p.get('camera_on_periods', []),
                'camera_on_count': p.get('camera_on_count', 0),
                'raw_user_qos_count': len(p.get('user_qos', []))
            })

        # Filter by search if provided
        if search_name:
            camera_data = [p for p in camera_data if search_name in p.get('user_name', '').lower() or search_name in p.get('email', '').lower()]
            return jsonify({
                'success': True,
                'meeting_id': meeting_id,
                'search': search_name,
                'matches_found': len(camera_data),
                'camera_data': camera_data,
                'note': 'Filtered by search term'
            })

        return jsonify({
            'success': True,
            'meeting_id': meeting_id,
            'total_participants': len(camera_data),
            'participants_with_camera': sum(1 for p in camera_data if p['camera_on_count'] > 0),
            'camera_data': camera_data[:50],  # Return 50 for preview, use search for specific
            'note': 'Use {"search": "name"} to find specific participant'
        })

    except Exception as e:
        print(f"[TestCameraQoS] Error: {e}")
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'meeting_id': meeting_id
        }), 500


# ==============================================================================
# RUN SERVER
# ==============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))

    print("=" * 60)
    print("BREAKOUT ROOM CALIBRATOR v2.0")
    print("=" * 60)

    # Load existing mappings from BigQuery (survives server restart)
    init_meeting_state()
    print(f"Port: {port}")
    print(f"GCP Project: {GCP_PROJECT_ID}")
    print(f"BigQuery Dataset: {BQ_DATASET}")
    print(f"Scout Bot Name: {SCOUT_BOT_NAME}")
    print()
    print("FLOW:")
    print("1. Start meeting at 9 AM")
    print("2. HR joins as 'Scout Bot'")
    print("3. Open Zoom App -> Run Calibration")
    print("4. Scout Bot can leave after calibration")
    print("5. Webhooks capture all participant activity")
    print("6. Daily report generated at 9:15 AM")
    print("=" * 60)

    app.run(host='0.0.0.0', port=port, debug=False)
