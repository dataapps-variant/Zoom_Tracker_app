# Zoom Breakout Room Tracker

A system to track participants in Zoom breakout rooms by mapping room UUIDs to human-readable names, with webhook-based real-time event capture.

## Problem Statement

Zoom webhooks provide breakout room events with **UUIDs** (e.g., `6kAkE8jOgeGj5m2DPy9/`) but **not room names**. This makes it impossible to know which room (e.g., "Cloud Gunners" or "HR Connect Room") a participant joined.

## Solution Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ZOOM BREAKOUT ROOM TRACKER                         │
└─────────────────────────────────────────────────────────────────────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Zoom Meeting │     │   Zoom App   │     │ Flask Server │     │   Storage    │
│  (Host + Bot) │     │  (React SDK) │     │  (Webhooks)  │     │ (BigQuery)   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │                    │
       │ 1. Create breakout │                    │                    │
       │    rooms           │                    │                    │
       │                    │                    │                    │
       │ 2. Open Zoom App ──┼────────────────────┤                    │
       │    (calibration)   │                    │                    │
       │                    │                    │                    │
       │                    │ 3. SDK gets room   │                    │
       │                    │    list with NAMES │                    │
       │                    │    & UUIDs         │                    │
       │                    │                    │                    │
       │                    │ 4. Send mapping ───┼──► Store UUID→Name │
       │                    │    to server       │    mapping         │
       │                    │                    │                    │
       │ 5. Participant     │                    │                    │
       │    joins room ─────┼────────────────────┼──► Webhook event   │
       │                    │                    │    (has UUID)      │
       │                    │                    │                    │
       │                    │                    │ 6. Enrich with ────┼──► Store with
       │                    │                    │    room NAME       │    room name!
       └────────────────────┴────────────────────┴────────────────────┴─────────────
```

## Components

### 1. Flask Server (`zoom_webhook_bigquery.py`)
- **Port**: 8080
- **Webhook endpoint**: `/webhook` - Receives Zoom events
- **Calibration endpoints**:
  - `POST /calibration/start` - Start calibration session
  - `POST /calibration/mapping` - Receive room mappings from SDK
  - `POST /calibration/complete` - Mark calibration done
- **Data endpoints**:
  - `GET /data` - View captured events
  - `GET /scout/all-mappings` - View all room name mappings
  - `GET /scout/status` - Check calibration status

### 2. React Zoom App (`breakout-calibrator/`)
- Uses `@zoom/appssdk` to access in-meeting data
- Gets breakout room list with names (only available via SDK, not REST API!)
- Sends mappings to Flask server
- Two calibration modes:
  - **Move Scout Bot**: Host moves another participant through rooms
  - **Move Myself**: Participant moves themselves through rooms

### 3. ngrok Tunnel
- Exposes localhost:8080 to the internet
- Required for Zoom webhooks and Zoom App URLs

## Why This Approach?

### The Problem with Zoom's API
1. **Webhooks don't include room names** - Only UUIDs like `6kAkE8jOgeGj5m2DPy9/`
2. **REST API doesn't work for PMR** - `/meetings/{id}/breakout_rooms` returns 404 for Personal Meeting Rooms
3. **SDK UUIDs differ from Webhook UUIDs** - SDK returns `{E7F123FC-...}` format, webhooks use base64-like format

### Our Solution
1. Use **Zoom Apps SDK** (runs inside the meeting) to get room names + SDK UUIDs
2. Store mapping: **Room Name → SDK UUID**
3. When webhooks arrive, we can't directly map UUID, but we have the room names for reference
4. For full tracking, the calibration captures room names which can be matched by position/index

## Initial Setup (One-Time)

### Prerequisites
- Python 3.8+
- Node.js 16+
- Zoom App credentials (created in Zoom Marketplace)
- ngrok account

### 1. Install Dependencies

```bash
# Backend
pip install -r requirements.txt

# Frontend (Zoom App)
cd breakout-calibrator
npm install
```

### 2. Configure Zoom App

1. Go to [Zoom Marketplace](https://marketplace.zoom.us/)
2. Create a "Zoom App" (Meeting SDK type)
3. Configure URLs (update with your ngrok URL):
   - **Home URL**: `https://<ngrok-url>/app`
   - **OAuth Redirect URL**: `https://<ngrok-url>/app`
4. Add Scopes: `zoomapp:inmeeting`
5. Add SDK Capabilities:
   - `getBreakoutRoomList`
   - `getMeetingParticipants`
   - `assignParticipantToBreakoutRoom`
   - `changeBreakoutRoom`
   - `getMeetingContext`
   - `getMeetingUUID`
   - `getUserContext`

### 3. Configure Webhooks

1. In your Zoom App settings, enable Event Subscriptions
2. **Webhook URL**: `https://<ngrok-url>/webhook`
3. **Subscribe to events**:
   - `meeting.participant_joined_breakout_room`
   - `meeting.participant_left_breakout_room`
   - `meeting.participant_joined`
   - `meeting.participant_left`
4. Copy the **Secret Token** and update in `zoom_webhook_bigquery.py`:
   ```python
   ZOOM_WEBHOOK_SECRET = 'your-secret-token'
   ```

### 4. Build React App

```bash
cd breakout-calibrator
npm run build
```

---

## Daily Usage Steps

### Step 1: Start ngrok
```bash
ngrok http 8080
```
Copy the HTTPS URL (e.g., `https://abc123.ngrok-free.app`)

### Step 2: Update Zoom App URLs (if ngrok URL changed)
Go to Zoom Marketplace → Your App → Update:
- Home URL: `https://<new-ngrok-url>/app`
- OAuth Redirect URL: `https://<new-ngrok-url>/app`
- Webhook URL: `https://<new-ngrok-url>/webhook`

### Step 3: Start Flask Server
```bash
python zoom_webhook_bigquery.py
```

### Step 4: Start Zoom Meeting
1. Start your meeting as host
2. Create/open breakout rooms (must be OPEN, not just created)
3. Optional: Have "Scout Bot" participant join

### Step 5: Run Calibration
1. In Zoom meeting, click "Apps" → Find your Zoom App ("Breakout Calibrator")
2. Open the app
3. Click **"Move Scout Bot"** (if you have a Scout Bot participant) or **"Move Myself"**
4. Wait for all rooms to be mapped (shows progress)

### Step 6: Verify Mappings
```bash
curl http://localhost:8080/scout/all-mappings
```

---

## File Structure

```
zoom+tracker/
├── zoom_webhook_bigquery.py    # Main Flask server (webhooks + API)
├── requirements.txt            # Python dependencies
├── bigquery_setup.sql          # BigQuery schema (optional cloud storage)
├── Dockerfile                  # For cloud deployment
├── README.md                   # This file
├── TOMORROW_STEPS.md           # Quick daily setup checklist
└── breakout-calibrator/        # React Zoom App
    ├── package.json
    ├── public/
    │   └── index.html
    └── src/
        ├── App.js
        ├── index.js
        ├── components/
        │   ├── CalibrationPanel.jsx   # Main UI component
        │   ├── StatusMessage.jsx
        │   ├── ProgressIndicator.jsx
        │   └── RoomList.jsx
        ├── hooks/
        │   └── useZoomSdk.js          # Zoom SDK integration
        └── services/
            ├── apiService.js          # Backend API calls
            └── zoomService.js         # Calibration logic
```

---

## How the Calibration Works

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CALIBRATION FLOW                                 │
└─────────────────────────────────────────────────────────────────────┘

1. User clicks "Start Calibration" in Zoom App
                    │
                    ▼
2. SDK calls getBreakoutRoomList()
   Returns: [
     { breakoutRoomId: "{E7F123FC-...}", name: "1.1:It's Accrual World" },
     { breakoutRoomId: "{3F9F659C-...}", name: "1.2:Between The Spreadsheet" },
     ...
   ]
                    │
                    ▼
3. SDK calls getMeetingParticipants()
   Returns: [
     { participantUUID: "abc123", name: "Scout Bot" },
     { participantUUID: "def456", name: "John Doe" },
     ...
   ]
                    │
                    ▼
4. Find "Scout Bot" in participant list
                    │
                    ▼
5. For each room:
   └─► SDK calls assignParticipantToBreakoutRoom({
         participantUUID: "abc123",
         breakoutRoomUUID: "{E7F123FC-...}"
       })
   └─► Wait 3 seconds for move to complete
   └─► Record mapping: UUID → Room Name
                    │
                    ▼
6. Send all mappings to Flask server:
   POST /calibration/mapping
   {
     "room_mapping": [
       { "room_uuid": "{E7F123FC-...}", "room_name": "1.1:It's Accrual World" },
       ...
     ]
   }
                    │
                    ▼
7. Server stores mappings in memory:
   uuid_to_name["{E7F123FC-...}"] = "1.1:It's Accrual World"
   uuid_to_name["E7F123FC-..."] = "1.1:It's Accrual World"  # Also stripped version
```

### Key Code Locations

| What | File | Function |
|------|------|----------|
| SDK initialization | `useZoomSdk.js` | `useEffect` hook |
| Get room list | `useZoomSdk.js` | `getBreakoutRooms()` |
| Move participant | `useZoomSdk.js` | `moveParticipantToRoom()` |
| Calibration logic | `zoomService.js` | `runCalibration()` |
| Find Scout Bot | `zoomService.js` | `findScoutBot()` |
| UI component | `CalibrationPanel.jsx` | `handleStartCalibration()` |
| Store mappings | `zoom_webhook_bigquery.py` | `/calibration/mapping` endpoint |

---

## Room Mapping Storage

The server stores mappings in multiple formats:

```python
room_mapper = {
    # Look up room name by UUID
    'uuid_to_name': {
        '{E7F123FC-...}': '1.1:It\'s Accrual World',  # SDK format with braces
        'E7F123FC-...': '1.1:It\'s Accrual World',    # Stripped format
    },

    # Look up UUID by room name
    'name_to_uuid': {
        '1.1:It\'s Accrual World': '{E7F123FC-...}'
    },

    # Full SDK mapping data with room index
    'sdk_mappings': {
        '1.1:It\'s Accrual World': {
            'sdk_uuid': '{E7F123FC-...}',
            'room_index': 0
        }
    }
}
```

---

## API Reference

### Webhook Endpoint

**POST /webhook**
- Receives Zoom webhook events
- Handles URL validation challenge automatically
- Stores events in memory and optionally BigQuery

### Data Endpoints

**GET /data**
```json
{
  "events": [...],
  "total_count": 150
}
```

**GET /scout/all-mappings**
```json
{
  "uuid_to_name": {...},
  "name_to_uuid": {...},
  "sdk_mappings": {...},
  "total_mappings": 132
}
```

**GET /scout/status**
```json
{
  "mapping_complete": true,
  "rooms_mapped": 132,
  "meeting_id": "9034027764"
}
```

### Calibration Endpoints

**POST /calibration/start**
```json
{
  "meeting_id": "9034027764",
  "meeting_uuid": "abc123..."
}
```

**POST /calibration/mapping**
```json
{
  "meeting_id": "9034027764",
  "room_mapping": [
    { "room_uuid": "{E7F123FC-...}", "room_name": "1.1:It's Accrual World" }
  ]
}
```

---

## Troubleshooting

### "Webhook validation failed"
- Check `ZOOM_WEBHOOK_SECRET` matches Zoom App's Secret Token
- Ensure ngrok is running and URL is updated in Zoom App

### "Scout Bot not found"
- Ensure participant named "Scout Bot" is in the meeting
- Check browser console (F12) for participant list debug logs
- The bot name matching is case-insensitive and partial

### "Bot not moving to rooms"
- Verify you're the host or co-host
- Ensure breakout rooms are **OPEN** (not just created)
- Check browser console for SDK error messages
- Try "Move Myself" mode if host-based movement fails

### "SDK UUID doesn't match webhook UUID"
- This is expected behavior - Zoom uses different UUID formats internally
- Use **room NAMES** as the common key for matching
- The system stores both formats for flexibility

### "Mappings lost after server restart"
- Currently mappings are stored in memory only
- Re-run calibration after restarting the server
- Future enhancement: persist to file or database

---

## Zoom SDK Methods Used

| Method | Purpose | Who Can Use |
|--------|---------|-------------|
| `getBreakoutRoomList()` | Get all rooms with names and UUIDs | Any participant |
| `getMeetingParticipants()` | Get all participants with their UUIDs | Any participant |
| `assignParticipantToBreakoutRoom()` | Move another participant to a room | Host/Co-host only |
| `changeBreakoutRoom()` | Move yourself to a room | Any participant |

---

## Webhook Events Captured

| Event | Data |
|-------|------|
| `meeting.participant_joined_breakout_room` | participant_id, room_uuid, timestamp |
| `meeting.participant_left_breakout_room` | participant_id, room_uuid, timestamp |
| `meeting.participant_joined` | participant_id, user_name, email |
| `meeting.participant_left` | participant_id, leave_reason |

---

## Cloud Deployment (Optional)

For production use, deploy to Google Cloud Run:

```bash
# Build and push to Artifact Registry
gcloud builds submit --tag us-central1-docker.pkg.dev/YOUR_PROJECT/zoom-tracker/webhook:latest

# Deploy to Cloud Run
gcloud run deploy zoom-webhook \
  --image us-central1-docker.pkg.dev/YOUR_PROJECT/zoom-tracker/webhook:latest \
  --region us-central1 \
  --allow-unauthenticated \
  --port 8080 \
  --set-env-vars "ZOOM_WEBHOOK_SECRET=your-secret,GCP_PROJECT_ID=your-project"
```

See `bigquery_setup.sql` for BigQuery table schemas.

---

## Future Enhancements

1. **Persist mappings** - Save to file/database for survival across restarts
2. **QoS data capture** - Add participant quality metrics (camera, audio)
3. **Auto-recalibration** - Detect when rooms change and re-map automatically
4. **Report generation** - Generate daily attendance reports with room names

---

## License

MIT License
