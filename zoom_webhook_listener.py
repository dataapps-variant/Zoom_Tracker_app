
from flask import Flask, request, jsonify
import csv
import os
import json
from datetime import datetime

# ==============================================================================
# SMART ZOOM LISTENER (DEBUG MODE)
# ==============================================================================
# This version logs the FULL RAW JSON from Zoom so we can find the hidden Room Name.

app = Flask(__name__)

LOG_FILE = "breakout_room_attendance_log.csv"
DEBUG_FILE = "zoom_raw_payloads.json"

# Initialize CSV
if not os.path.exists(LOG_FILE):
    with open(LOG_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["Timestamp", "Meeting ID", "User ID", "User Name", "Event Type", "Possible Room ID"])

@app.route('/webhook', methods=['GET', 'POST'])
def zoom_webhook():
    # Handle GET for browser testing
    if request.method == 'GET':
        return jsonify({"status": "Webhook is running! Use POST for actual events."}), 200
    
    data = request.json
    event = data.get('event')
    
    print(f"Received event: {event}")  # Debug log
    
    # 1. Verification Challenge
    if event == 'endpoint.url_validation':
        plain_token = data.get('payload', {}).get('plainToken')
        print(f"Validating with token: {plain_token}")  # Debug log
        import hmac, hashlib
        SECRET_TOKEN = os.environ.get('ZOOM_SECRET_TOKEN', 'YOUR_SECRET_TOKEN_HERE') 
        hash_for_validate = hmac.new(key=SECRET_TOKEN.encode('utf-8'), msg=plain_token.encode('utf-8'), digestmod=hashlib.sha256).hexdigest()
        print(f"Returning hash: {hash_for_validate}")  # Debug log
        return jsonify({"plainToken": plain_token, "encryptedToken": hash_for_validate}), 200

    # 2. LOG EVERYTHING (To find the missing data)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_FILE, 'a') as f:
        f.write(json.dumps(data, indent=2) + ",\n")

    # 3. Try to extract data from ANY join/left event
    payload = data.get('payload', {}).get('object', {})
    participant = payload.get('participant', {})
    
    # Check if this generic 'join' event has breakout room data hidden in it
    user_name = participant.get('user_name', 'Unknown')
    user_id = participant.get('user_id', '')
    
    # Sometimes room info is in the root, or inside 'participant'
    # We look for ANY key that resembles 'room_id'
    potential_room = "Main Meeting"
    if 'room_id' in payload:
        potential_room = f"Room {payload['room_id']}"
    elif 'room_name' in payload:
        potential_room = payload['room_name']
        
    print(f"[{timestamp}] Event: {event} | User: {user_name} | Location: {potential_room}")

    # Write to clean log
    with open(LOG_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([timestamp, payload.get('id'), user_id, user_name, event, potential_room])

    return jsonify({"status": "success"}), 200

if __name__ == '__main__':
    print("Starting Listener...")
    app.run(host='0.0.0.0', port=5000)
