# Zoom Marketplace App Setup - Step by Step

## Step 1: Access Zoom Marketplace Developer Portal

1. Go to **https://marketplace.zoom.us**
2. Sign in with your Zoom account (must be Pro/Business/Enterprise)
3. Click **Develop** in the top menu
4. Click **Build App**

## Step 2: Create New Zoom App

1. Click **Create** button
2. Select **Zoom Apps** as the app type (NOT OAuth App, NOT Webhook)
3. Click **Create**

## Step 3: Basic Information

Fill in these fields:

| Field | Value |
|-------|-------|
| **App Name** | `Breakout Room Calibrator` |
| **Short Description** | `Automatically maps breakout room names for tracking` |
| **Long Description** | `This app runs inside Zoom and automatically moves a scout bot through all breakout rooms to discover room names. It maps room UUIDs to human-readable names for attendance tracking.` |
| **Company Name** | Your company name |
| **Developer Name** | Your name |
| **Developer Email** | Your email |

Click **Continue**

## Step 4: Configure OAuth (Authentication)

### Redirect URL
```
https://YOUR-NGROK-URL.ngrok.io/auth/callback
```

For local development, use ngrok:
```bash
ngrok http 3000
# Copy the https URL, e.g., https://abc123.ngrok.io
```

### OAuth Scopes
Add this scope:
- `zoomapp:inmeeting` - Required for in-meeting APIs

Click **Continue**

## Step 5: Configure App Features

### Home URL (where your app loads)
```
https://YOUR-NGROK-URL.ngrok.io
```

### Zoom App SDK
Toggle **ON** to enable the SDK

### Capabilities (IMPORTANT - add all of these)

Click **Add** next to each capability:

| Capability | Description |
|------------|-------------|
| `getBreakoutRoomList` | Get all breakout rooms with names |
| `getMeetingParticipants` | Get participant list to find scout bot |
| `assignParticipantToBreakoutRoom` | Move scout bot to rooms |
| `getMeetingContext` | Get current meeting info |
| `getMeetingUUID` | Get meeting unique ID |
| `getUserContext` | Check if user is host |
| `onBreakoutRoomChange` | Listen for room changes |
| `onParticipantChange` | Listen for participant changes |
| `onMeetingConfigChanged` | Listen for meeting config changes |

Click **Continue**

## Step 6: App Credentials

**SAVE THESE VALUES** - you'll need them for your `.env` file:

```
Client ID: ________________________
Client Secret: ____________________
```

## Step 7: Configure Your Environment

Create `.env` file in `breakout-calibrator/`:

```bash
# Zoom App Credentials (from Step 6)
REACT_APP_ZOOM_CLIENT_ID=your_client_id_here
REACT_APP_ZOOM_CLIENT_SECRET=your_client_secret_here
REACT_APP_REDIRECT_URL=https://YOUR-NGROK-URL.ngrok.io/auth/callback

# Backend URL (your Cloud Run URL or local)
REACT_APP_BACKEND_URL=https://your-cloud-run-url.run.app

# Scout Bot (must match your existing config)
REACT_APP_BOT_NAME=Scout Bot
REACT_APP_BOT_EMAIL=scout@yourdomain.com
```

## Step 8: Install & Run Locally

```bash
# Terminal 1: Start ngrok
ngrok http 3000

# Terminal 2: Start React app (in breakout-calibrator folder)
cd breakout-calibrator
npm install
set HTTPS=true && npm start    # Windows
# or
HTTPS=true npm start           # Mac/Linux

# Terminal 3: Start backend server
cd breakout-calibrator/server
npm install
npm start
```

## Step 9: Test in Zoom

1. Open **Zoom Desktop Client** (version 5.11+)
2. Start or join a meeting
3. Click **Apps** in the meeting toolbar
4. Find **Breakout Room Calibrator**
5. Click to open

### If app doesn't appear:
- Make sure you're signed into Zoom with the same account
- Development apps only work for the developer account
- Check that ngrok is running and URLs are updated

## Step 10: Create Breakout Rooms & Test

1. In Zoom meeting, create breakout rooms
2. Open the breakout rooms (they must be OPEN, not just created)
3. Have your scout bot join the meeting
4. In the Calibrator app, click **Start Calibration**
5. Watch as the bot moves through each room
6. Check your backend for the room mappings

## Troubleshooting

### "SDK not configured"
- Check all capabilities are added in Marketplace
- Make sure HTTPS is enabled for React app
- Verify ngrok URL matches Marketplace settings

### "Only hosts can use this app"
- The app must be opened by the meeting HOST or CO-HOST
- Regular participants cannot move others

### "Scout bot not found"
- Make sure bot has joined the meeting BEFORE starting calibration
- Check `REACT_APP_BOT_NAME` matches exactly

### "No breakout rooms found"
- Rooms must be CREATED and OPENED
- Check Zoom client version (5.11+ recommended)

### App not appearing in Zoom
- Development apps only work for developer's account
- Make sure Zoom client is up to date
- Try signing out and back in to Zoom

## Production Deployment

For production (other users):
1. Build React app: `npm run build`
2. Deploy to Vercel/Netlify/Cloud Run
3. Update all URLs in Marketplace
4. Submit for Zoom review (required for public apps)

## Reference Links

- [Zoom Apps Documentation](https://developers.zoom.us/docs/zoom-apps/)
- [Zoom Apps SDK Reference](https://developers.zoom.us/docs/zoom-apps/js-sdk/reference/)
- [Marketplace Developer Portal](https://marketplace.zoom.us/develop)
