"""
ZOOM WEBHOOK LISTENER - GCP CLOUD RUN + GCS + BIGQUERY
=======================================================

WORKFLOW:
1. Receives webhook events from Zoom
2. Writes raw JSON to GCS bucket (backup/archive)
3. Streams to BigQuery for immediate querying

WHY GCS + BIGQUERY:
- GCS: Cheap storage, keeps original JSON forever, batch loading is free
- BigQuery: Fast queries, transformations, scheduled reports

FIELDS CAPTURED:
- event_id: Unique ID for each event
- event_type: "joined" or "left"
- event_timestamp: When the event happened
- participant_name: Who joined/left
- participant_email: Their email
- breakout_room_uuid: Which room
- raw_payload: Full JSON for debugging
"""

from flask import Flask, request, jsonify
from google.cloud import bigquery
from google.cloud import storage
from datetime import datetime
import hmac
import hashlib
import json
import os
import uuid

# ==============================================================================
# CONFIGURATION
# ==============================================================================

app = Flask(__name__)

# Environment variables (set in Cloud Run)
ZOOM_WEBHOOK_SECRET = os.environ.get('ZOOM_WEBHOOK_SECRET', 'r72xUnMLTHOgHcgZS3Np7Q')
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'your-project-id')

# GCS Configuration
GCS_BUCKET = os.environ.get('GCS_BUCKET', 'zoom-tracker-data')
GCS_RAW_PREFIX = os.environ.get('GCS_RAW_PREFIX', 'raw')

# BigQuery Configuration
BQ_DATASET = os.environ.get('BQ_DATASET', 'zoom_tracker')
BQ_TABLE = os.environ.get('BQ_TABLE', 'raw_events')

# Clients (initialized lazily)
bq_client = None
gcs_client = None

def get_bq_client():
    """Get or create BigQuery client"""
    global bq_client
    if bq_client is None:
        bq_client = bigquery.Client(project=GCP_PROJECT_ID)
    return bq_client

def get_gcs_client():
    """Get or create GCS client"""
    global gcs_client
    if gcs_client is None:
        gcs_client = storage.Client(project=GCP_PROJECT_ID)
    return gcs_client

# ==============================================================================
# GCS FUNCTIONS
# ==============================================================================

def write_to_gcs(event_data):
    """
    Write event to Google Cloud Storage

    WHY GCS:
    - Cheap storage ($0.02/GB/month)
    - Original JSON preserved forever
    - Can reload to BigQuery if schema changes
    - Batch load to BigQuery is FREE (streaming costs $$$)

    FILE STRUCTURE:
    gs://bucket/raw/2026-02-03/events.jsonl
    - One JSON object per line (JSON Lines format)
    - Easy to load into BigQuery
    """
    try:
        client = get_gcs_client()
        bucket = client.bucket(GCS_BUCKET)

        # Create file path: raw/YYYY-MM-DD/events.jsonl
        today = datetime.utcnow().strftime('%Y-%m-%d')
        blob_path = f"{GCS_RAW_PREFIX}/{today}/events.jsonl"

        blob = bucket.blob(blob_path)

        # Append to existing file or create new
        # Using JSON Lines format (one JSON per line)
        json_line = json.dumps(event_data) + '\n'

        # Download existing content if file exists, then append
        try:
            existing_content = blob.download_as_text()
        except:
            existing_content = ''

        new_content = existing_content + json_line
        blob.upload_from_string(new_content, content_type='application/json')

        print(f"  -> GCS: Written to {blob_path}")
        return True

    except Exception as e:
        print(f"  -> GCS Error: {e}")
        return False

def write_to_gcs_individual(event_data):
    """
    Alternative: Write each event as individual file

    WHY INDIVIDUAL FILES:
    - No read-modify-write (faster)
    - Parallel processing possible
    - Better for high volume

    FILE STRUCTURE:
    gs://bucket/raw/2026-02-03/event_uuid.json
    """
    try:
        client = get_gcs_client()
        bucket = client.bucket(GCS_BUCKET)

        # Create unique file path
        today = datetime.utcnow().strftime('%Y-%m-%d')
        event_id = event_data.get('event_id', str(uuid.uuid4()))
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
    """
    Stream event to BigQuery for immediate querying

    WHY STREAM TO BIGQUERY:
    - Immediate availability (< 1 second)
    - Can query data right away
    - Real-time dashboards possible

    NOTE: Streaming inserts cost $0.01 per 200MB
    For high volume, use batch load from GCS instead
    """
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
# EVENT PARSING
# ==============================================================================

def parse_zoom_event(data):
    """
    Parse Zoom webhook payload into structured format

    FIELDS EXTRACTED:
    - event_id: UUID we generate for uniqueness
    - event_date: Date of the event (YYYY-MM-DD) - for filtering reports
    - event_type: Full Zoom event name
    - event_timestamp: When Zoom sent the event
    - meeting_id: Zoom meeting ID
    - meeting_uuid: Unique meeting instance
    - participant_id: Zoom's user ID
    - participant_name: Display name
    - participant_email: Email if available
    - breakout_room_uuid: Which room
    - action: Simplified JOIN/LEAVE
    - raw_payload: Original JSON
    - inserted_at: When we received it
    - gcs_path: Where raw file is stored
    """
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

    event_id = str(uuid.uuid4())
    today = datetime.utcnow().strftime('%Y-%m-%d')

    return {
        'event_id': event_id,
        'event_date': event_date,  # NEW: Date field for easy filtering
        'event_type': event,
        'event_timestamp': event_datetime,
        'meeting_id': obj.get('id', ''),
        'meeting_uuid': obj.get('uuid', ''),
        'participant_id': participant.get('user_id', ''),
        'participant_name': participant.get('user_name', 'Unknown'),
        'participant_email': participant.get('email', ''),
        'breakout_room_uuid': obj.get('breakout_room_uuid', ''),
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
    """Health check endpoint for Cloud Run"""
    return jsonify({
        'status': 'running',
        'service': 'Zoom Webhook → GCS + BigQuery',
        'config': {
            'project': GCP_PROJECT_ID,
            'bucket': GCS_BUCKET,
            'dataset': BQ_DATASET,
            'table': BQ_TABLE
        },
        'timestamp': datetime.utcnow().isoformat()
    }), 200

@app.route('/webhook', methods=['GET', 'POST'])
def zoom_webhook():
    """
    Main webhook endpoint

    FLOW:
    1. Receive event from Zoom
    2. Validate (for URL validation challenge)
    3. Parse event data
    4. Write to GCS (raw backup)
    5. Stream to BigQuery (immediate query)
    6. Return success to Zoom
    """
    if request.method == 'GET':
        return jsonify({
            'status': 'Webhook ready',
            'endpoints': {
                'webhook': '/webhook (POST)',
                'health': '/ (GET)',
                'test_gcs': '/test-gcs (GET)',
                'test_bq': '/test-bq (GET)'
            }
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

    # Process breakout room events
    if 'breakout_room' in event:
        row_data = parse_zoom_event(data)

        print(f"  -> {row_data['action']}: {row_data['participant_name']}")
        print(f"  -> Room: {row_data['breakout_room_uuid'][:20]}...")

        # Write to GCS (raw backup) - using individual files
        write_to_gcs_individual(row_data)

        # Stream to BigQuery (immediate query)
        write_to_bigquery(row_data)

    return jsonify({'status': 'success'}), 200

# ==============================================================================
# TEST ENDPOINTS
# ==============================================================================

@app.route('/test-gcs', methods=['GET'])
def test_gcs():
    """Test GCS connection"""
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
    """Test BigQuery connection"""
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

# ==============================================================================
# RUN SERVER
# ==============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("=" * 60)
    print("ZOOM WEBHOOK SERVER - GCS + BIGQUERY")
    print("=" * 60)
    print(f"Port: {port}")
    print(f"GCS Bucket: {GCS_BUCKET}")
    print(f"BigQuery: {GCP_PROJECT_ID}.{BQ_DATASET}.{BQ_TABLE}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=False)
