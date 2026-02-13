# Zoom Breakout Room Tracker - Quick Start Guide

## Daily Setup Steps (5 minutes)

### Step 1: Start ngrok
```bash
ngrok http 8080
```
Copy the HTTPS URL (e.g., `https://xxxxx.ngrok-free.app`)

### Step 2: Start Flask Server
```bash
cd C:\Users\shash\Downloads\zoom+tracker
python zoom_webhook_bigquery.py
```
Server runs on port 8080.

### Step 3: Update Zoom App URLs (only if ngrok URL changed)

Go to https://marketplace.zoom.us/ → Your Zoom App → Update:
- **Home URL:** `https://YOUR-NGROK-URL/app`
- **OAuth Redirect URL:** `https://YOUR-NGROK-URL/app`
- **Webhook URL:** `https://YOUR-NGROK-URL/webhook`

### Step 4: Start Zoom Meeting
1. Start your meeting as host
2. Create/open breakout rooms
3. **Important:** Breakout rooms must be OPEN (not just created)

### Step 5: Run Calibration
1. In Zoom meeting → Click **Apps** → Open "Breakout Calibrator"
2. Click **"Move Scout Bot"** or **"Move Myself"**
3. Wait for all rooms to map (shows progress bar)

### Step 6: Verify Success
```bash
curl http://localhost:8080/scout/all-mappings
```

---

## Quick Commands

```bash
# Check server health
curl http://localhost:8080/

# Check calibration status
curl http://localhost:8080/scout/status

# Get all room mappings
curl http://localhost:8080/scout/all-mappings

# Get captured events
curl http://localhost:8080/data
```

---

## Calibration Modes

| Mode | When to Use | Who |
|------|-------------|-----|
| **Move Scout Bot** | You're the host, Scout Bot is a participant | Host only |
| **Move Myself** | You want to move yourself through rooms | Anyone |

---

## Last Successful Test: 2026-02-13

- **66 rooms** successfully mapped
- **132 mappings** stored (with and without UUID braces)
- All room names captured: "1.1:It's Accrual World", "Cloud Gunners", etc.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Scout Bot not found" | Ensure participant named "Scout Bot" exists in meeting |
| "Webhook validation failed" | Check ZOOM_WEBHOOK_SECRET matches Zoom App settings |
| Bot not moving | Ensure you're host/co-host, rooms are OPEN |
| Mappings empty after restart | Re-run calibration (in-memory storage) |
| Blue/blank app screen | Rebuild app: `cd breakout-calibrator && npm run build` |

---

## Key Credentials (in zoom_webhook_bigquery.py)

```python
ZOOM_WEBHOOK_SECRET = 'HyA8GYp6Spy9WWSjW4_pjA'
ZOOM_ACCOUNT_ID = 'xhKbAsmnSM6pNYYYurmqIA'
ZOOM_CLIENT_ID = 'TqtBGqTAS3W1Jgf9a41w'
```

---

## File Locations

| File | Purpose |
|------|---------|
| `zoom_webhook_bigquery.py` | Main server |
| `breakout-calibrator/` | React Zoom App |
| `README.md` | Full documentation |
