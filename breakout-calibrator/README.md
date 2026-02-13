# Breakout Room Calibrator - Zoom App

A Zoom App that automatically moves a scout bot through all breakout rooms to map room UUIDs to human-readable names.

## How It Works

1. **Host opens the Zoom App** inside their Zoom desktop client
2. **Clicks "Start Calibration"**
3. **App automatically**:
   - Fetches all breakout room names via SDK
   - Finds the scout bot in participants
   - Moves the bot through each room (3 seconds per room)
   - Records the UUID → name mapping
   - Sends mappings to your backend

**Result**: All 66 rooms mapped in ~3 minutes with one click!

## Prerequisites

- Zoom Pro/Business/Enterprise account
- Zoom Desktop Client 5.11+
- Node.js 16+
- HTTPS endpoint (ngrok for development)

## Setup

### 1. Create Zoom App on Marketplace

1. Go to https://marketplace.zoom.us
2. Click **Develop** → **Build App**
3. Select **Zoom Apps** type
4. Fill in app details
5. Add OAuth scopes: `zoomapp:inmeeting`
6. Add capabilities:
   - getBreakoutRoomList
   - getMeetingParticipants
   - assignParticipantToBreakoutRoom
   - getMeetingContext
   - getMeetingUUID
   - getUserContext

### 2. Configure Environment

```bash
# Copy the example env file
cp .env.example .env

# Edit with your credentials
REACT_APP_ZOOM_CLIENT_ID=your_client_id
REACT_APP_ZOOM_CLIENT_SECRET=your_client_secret
REACT_APP_REDIRECT_URL=https://your-ngrok.io/auth/callback
REACT_APP_BACKEND_URL=http://localhost:3001
REACT_APP_BOT_NAME=Scout Bot
```

### 3. Install Dependencies

```bash
# Install React app dependencies
npm install

# Install server dependencies
cd server && npm install
```

### 4. Start Development

```bash
# Terminal 1: Start React app (with HTTPS for Zoom)
HTTPS=true npm start

# Terminal 2: Start backend server
npm run server
```

### 5. Use ngrok for HTTPS

```bash
ngrok http 3000
```

Update your Zoom App settings with the ngrok URL.

## Project Structure

```
breakout-calibrator/
├── src/
│   ├── components/
│   │   ├── CalibrationPanel.jsx   # Main UI
│   │   ├── RoomList.jsx           # Room display
│   │   ├── ProgressIndicator.jsx  # Progress bar
│   │   └── StatusMessage.jsx      # Status display
│   ├── hooks/
│   │   └── useZoomSdk.js          # Zoom SDK wrapper
│   ├── services/
│   │   ├── zoomService.js         # Calibration logic
│   │   └── apiService.js          # Backend API calls
│   ├── App.js
│   └── index.js
├── server/
│   ├── index.js                   # Express server
│   └── package.json
├── public/
│   └── index.html
├── package.json
└── .env.example
```

## API Endpoints (Backend)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/calibration/start` | POST | Start calibration session |
| `/calibration/mapping` | POST | Receive room mappings |
| `/calibration/complete` | POST | Complete calibration |
| `/calibration/mappings/:meetingId` | GET | Get existing mappings |

## Integration with Existing Backend

To integrate with your existing `zoom_webhook_bigquery.py`:

1. Add these routes to receive calibration data
2. Store mappings in BigQuery `room_mappings` table
3. The webhook handler will auto-enrich events with room names

## Calibration Flow

```
┌─────────────────────────────────────────────┐
│         HOST'S ZOOM CLIENT                  │
│  ┌───────────────────────────────────────┐  │
│  │     Breakout Room Calibrator App      │  │
│  │  ┌─────────────────────────────────┐  │  │
│  │  │   [Start Calibration]           │  │  │
│  │  │   Progress: ████████░░ 80%      │  │  │
│  │  │   Room 1: Math Class ✓          │  │  │
│  │  │   Room 2: Science Lab ✓         │  │  │
│  │  │   Room 3: English... [CURRENT]  │  │  │
│  │  └─────────────────────────────────┘  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
                    │
                    │ assignParticipantToBreakoutRoom()
                    ▼
┌─────────────────────────────────────────────┐
│              ZOOM MEETING                   │
│  Scout Bot moves: Room1 → Room2 → Room3...  │
└─────────────────────────────────────────────┘
                    │
                    │ POST /calibration/mapping
                    ▼
┌─────────────────────────────────────────────┐
│           YOUR BACKEND                      │
│  Stores: room_uuid → room_name             │
│  BigQuery: room_mappings table             │
└─────────────────────────────────────────────┘
```

## Troubleshooting

### "SDK not configured"
- Make sure you're running inside Zoom desktop client
- Check that all capabilities are added in Zoom Marketplace

### "Only hosts can use this app"
- The app must be opened by the meeting host or co-host
- Regular participants cannot move others to breakout rooms

### "Scout bot not found"
- Make sure the bot has joined the meeting
- Check `REACT_APP_BOT_NAME` matches the bot's display name

### "No breakout rooms found"
- Create breakout rooms before starting calibration
- Make sure breakout rooms are **open** (not just created)

## Production Deployment

1. Build the React app: `npm run build`
2. Deploy to your hosting (Vercel, Netlify, Cloud Run)
3. Update Zoom App settings with production URLs
4. Submit app for Zoom review (required for other users)
