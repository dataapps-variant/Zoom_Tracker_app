# Zoom Breakout Room Tracker

Track participant movement across Zoom breakout rooms with camera activity data.

## Features
- Captures breakout room join/leave events via Zoom webhooks
- Tracks camera on/off time using Zoom QOS API
- Generates detailed CSV reports with:
  - Participant name and email
  - Meeting join/leave times
  - Room-by-room journey tracking
  - Camera usage statistics

## Quick Start (Cloud Deployment - Recommended)

### Step 1: Deploy to Render (Free)

1. **Create Render Account**
   - Go to https://render.com and sign up (free)

2. **Connect GitHub**
   - Click "New" -> "Web Service"
   - Connect your GitHub account
   - Select this repository: `dataapps-variant/Zoom_Tracker`

3. **Configure Service**
   ```
   Name: zoom-webhook
   Region: Oregon (or closest to you)
   Branch: main
   Runtime: Python 3
   Build Command: pip install -r requirements.txt
   Start Command: gunicorn zoom_webhook_listener:app
   Instance Type: Free
   ```

4. **Add Environment Variable**
   - Go to "Environment" tab
   - Add: `ZOOM_WEBHOOK_SECRET` = `r72xUnMLTHOgHcgZS3Np7Q`

5. **Deploy**
   - Click "Create Web Service"
   - Wait for deployment (2-3 minutes)
   - Copy your URL: `https://zoom-webhook-xxxx.onrender.com`

### Step 2: Update Zoom Webhook URL

1. Go to https://marketplace.zoom.us/
2. Open your Server-to-Server OAuth app
3. Go to "Feature" -> "Event Subscriptions"
4. Update webhook URL to: `https://your-render-url.onrender.com/webhook`
5. Click "Save"

### Step 3: Generate Reports

After each meeting, run locally:

```bash
# Install dependencies (once)
pip install requests

# Generate today's report
python download_and_report.py --webhook-url https://your-render-url.onrender.com

# Generate specific date report
python download_and_report.py 2026-02-02 --webhook-url https://your-render-url.onrender.com
```

## Alternative: Local Setup

If you prefer running locally:

```bash
# Install dependencies
pip install flask requests

# Start webhook server
python zoom_webhook_listener.py

# In another terminal, expose via ngrok
ngrok http 5000

# Update Zoom webhook URL to ngrok URL
# Run meeting
# Generate report
python generate_daily_report.py
```

## File Structure

```
zoom+tracker/
├── zoom_webhook_listener.py   # Cloud-ready webhook server
├── download_and_report.py     # Download data & generate reports
├── generate_daily_report.py   # Local report generator (standalone)
├── requirements.txt           # Python dependencies
├── Procfile                   # Cloud deployment config
└── reports/                   # Generated CSV reports
```

## API Endpoints

When deployed, your webhook provides:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/webhook` | POST | Zoom webhook receiver |
| `/data` | GET | Download all captured events |
| `/data/today` | GET | Download today's events only |
| `/data/clear` | POST | Clear stored data |
| `/status` | GET | Detailed status info |

## Configuration

### Zoom App Settings

Required scopes:
- `meeting:read:meeting:admin`
- `dashboard_meetings:read:admin`

Webhook events to subscribe:
- `meeting.participant_joined_breakout_room`
- `meeting.participant_left_breakout_room`

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ZOOM_WEBHOOK_SECRET` | Zoom webhook secret token | (required) |
| `PORT` | Server port | 5000 |

## Report Output

CSV columns:
1. Participant Name
2. Email ID
3. Meeting Join Time
4. Meeting Left Time
5. Meeting Duration (mins)
6. Room Name
7. Room Join Time
8. Room Left Time
9. Room Duration (mins)
10. Camera ON (mins)
11. Camera OFF (mins)
12. Camera %
13. Next Room

## Troubleshooting

**No data captured?**
- Ensure webhook URL is correct in Zoom app
- Check Render logs for incoming events
- Verify webhook events are enabled in Zoom

**Camera data shows 0%?**
- QOS data requires ~24 hours to be available
- Meeting must have Dashboard access enabled

**Connection errors?**
- Render free tier may sleep after 15 min inactivity
- First request wakes it up (may take 30 seconds)

## License

MIT
