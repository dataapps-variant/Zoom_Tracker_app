
from flask import Flask, request, jsonify
import csv
import os
import json
from datetime import datetime
import hmac
import hashlib

# ==============================================================================
# ZOOM WEBHOOK LISTENER - CLOUD READY
# ==============================================================================
# Captures breakout room join/leave events from Zoom webhooks
# Deploy to: Render, Railway, or any cloud platform

app = Flask(__name__)

# Configuration from environment variables (set these in your cloud platform)
SECRET_TOKEN = os.environ.get('ZOOM_WEBHOOK_SECRET', 'r72xUnMLTHOgHcgZS3Np7Q')

# File storage (works on cloud but ephemeral - data persists until redeploy)
DATA_DIR = os.environ.get('DATA_DIR', '.')
LOG_FILE = os.path.join(DATA_DIR, "breakout_room_attendance_log.csv")
DEBUG_FILE = os.path.join(DATA_DIR, "zoom_raw_payloads.json")

# In-memory storage (backup for when files are not available)
memory_storage = []

def ensure_files():
    """Create files if they don't exist"""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Meeting ID", "User ID", "User Name", "Event Type", "Room Info"])
    if not os.path.exists(DEBUG_FILE):
        with open(DEBUG_FILE, 'w') as f:
            f.write('[]')

ensure_files()

# ==============================================================================
# HEALTH CHECK ENDPOINT
# ==============================================================================
@app.route('/', methods=['GET'])
def health_check():
    """Health check for cloud platforms"""
    return jsonify({
        "status": "running",
        "service": "Zoom Webhook Listener",
        "events_captured": len(memory_storage),
        "timestamp": datetime.now().isoformat()
    }), 200

# ==============================================================================
# MAIN WEBHOOK ENDPOINT
# ==============================================================================
@app.route('/webhook', methods=['GET', 'POST'])
def zoom_webhook():
    # Handle GET for browser testing
    if request.method == 'GET':
        return jsonify({
            "status": "Webhook is running!",
            "events_in_memory": len(memory_storage),
            "message": "Use POST for actual Zoom events."
        }), 200

    data = request.json
    event = data.get('event')

    print(f"[{datetime.now()}] Received event: {event}")

    # 1. Verification Challenge (Required by Zoom)
    if event == 'endpoint.url_validation':
        plain_token = data.get('payload', {}).get('plainToken')
        print(f"Validating webhook with token: {plain_token[:10]}...")
        hash_for_validate = hmac.new(
            key=SECRET_TOKEN.encode('utf-8'),
            msg=plain_token.encode('utf-8'),
            digestmod=hashlib.sha256
        ).hexdigest()
        return jsonify({
            "plainToken": plain_token,
            "encryptedToken": hash_for_validate
        }), 200

    # 2. Add timestamp and store in memory
    data['event_ts'] = int(datetime.now().timestamp() * 1000)
    memory_storage.append(data)

    # 3. Save to file (with error handling for cloud environments)
    try:
        with open(DEBUG_FILE, 'r') as f:
            content = f.read().strip()
            if content and content != '[]':
                existing = json.loads(content if content.startswith('[') else '[' + content.rstrip(',\n') + ']')
            else:
                existing = []
        existing.append(data)
        with open(DEBUG_FILE, 'w') as f:
            json.dump(existing, f, indent=2)
    except Exception as e:
        print(f"File write error (using memory): {e}")

    # 4. Extract event info for logging
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload = data.get('payload', {}).get('object', {})
    participant = payload.get('participant', {})

    user_name = participant.get('user_name', 'Unknown')
    user_id = participant.get('user_id', '')
    room_uuid = payload.get('breakout_room_uuid', 'Main Meeting')

    print(f"[{timestamp}] {event} | User: {user_name} | Room: {room_uuid[:20]}...")

    # Write to CSV log
    try:
        with open(LOG_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, payload.get('id'), user_id, user_name, event, room_uuid])
    except Exception as e:
        print(f"CSV write error: {e}")

    return jsonify({"status": "success"}), 200

# ==============================================================================
# DATA DOWNLOAD ENDPOINTS
# ==============================================================================
@app.route('/data', methods=['GET'])
def get_data():
    """Download all captured webhook data as JSON"""
    # Try file first, fall back to memory
    try:
        with open(DEBUG_FILE, 'r') as f:
            content = f.read().strip()
            if content and content != '[]':
                data = json.loads(content if content.startswith('[') else '[' + content.rstrip(',\n') + ']')
                return jsonify({"count": len(data), "events": data}), 200
    except:
        pass

    return jsonify({"count": len(memory_storage), "events": memory_storage}), 200

@app.route('/data/today', methods=['GET'])
def get_today_data():
    """Get only today's events"""
    today = datetime.now().date()

    try:
        with open(DEBUG_FILE, 'r') as f:
            content = f.read().strip()
            if content and content != '[]':
                all_data = json.loads(content if content.startswith('[') else '[' + content.rstrip(',\n') + ']')
            else:
                all_data = memory_storage
    except:
        all_data = memory_storage

    today_data = []
    for event in all_data:
        ts = event.get('event_ts', 0)
        if ts:
            event_date = datetime.fromtimestamp(ts/1000).date()
            if event_date == today:
                today_data.append(event)

    return jsonify({"date": str(today), "count": len(today_data), "events": today_data}), 200

@app.route('/data/clear', methods=['POST'])
def clear_data():
    """Clear all stored data (use after downloading)"""
    global memory_storage
    memory_storage = []
    try:
        with open(DEBUG_FILE, 'w') as f:
            f.write('[]')
        with open(LOG_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Timestamp", "Meeting ID", "User ID", "User Name", "Event Type", "Room Info"])
    except:
        pass
    return jsonify({"status": "cleared"}), 200

@app.route('/status', methods=['GET'])
def status():
    """Detailed status for debugging"""
    try:
        with open(DEBUG_FILE, 'r') as f:
            content = f.read().strip()
            file_count = len(json.loads(content if content.startswith('[') else '[' + content.rstrip(',\n') + ']')) if content and content != '[]' else 0
    except:
        file_count = 0

    return jsonify({
        "status": "online",
        "memory_events": len(memory_storage),
        "file_events": file_count,
        "server_time": datetime.now().isoformat()
    }), 200

# ==============================================================================
# RUN SERVER
# ==============================================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Zoom Webhook Listener on port {port}...")
    print(f"Webhook URL: http://localhost:{port}/webhook")
    print(f"Data URL: http://localhost:{port}/data")
    app.run(host='0.0.0.0', port=port)
