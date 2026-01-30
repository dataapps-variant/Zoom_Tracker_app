# Zoom Breakout Room & Video Tracker

## Overview
This system tracks:
1. **Breakout Room Movements** - Who joined/left which room and when
2. **Video Camera Status** - Whether camera was ON or OFF (available after meeting ends)

---

## Files in This Project

| File | Purpose |
|------|---------|
| `zoom_webhook_listener.py` | Captures room movements in real-time |
| `urgent_qos_capture.py` | Gets camera ON/OFF data after meeting ends |
| `generate_detailed_report.py` | Creates the final combined report |
| `room_names.json` | Mapping of room names |
| `breakout_room_attendance_log.csv` | Captured room movement data |
| `zoom_raw_payloads.json` | Raw webhook data from Zoom |

---

# **TOMORROW MORNING INSTRUCTIONS**

## **⏰ When Meeting Ends (9:00 AM)**

**Wait 15-30 minutes for Zoom to process the data.**

---

## **⏰ At 9:30 AM - Run These Commands:**

Open PowerShell and run:

```powershell
cd c:\Users\shash\Downloads\zoom+tracker

# Step 1: Get Camera ON/OFF Data
python urgent_qos_capture.py

# Step 2: Generate Final Report
python generate_detailed_report.py
```

---

## **📊 Output Files:**

After running the scripts, you will get:

1. **`QOS_CAMERA_REPORT_XXXXXX.csv`** - Camera ON/OFF per participant
2. **`DETAILED_ROOM_REPORT.csv`** - Room movements with timing

**Open these files in Excel!**

---

## **🔧 If Script Stopped Overnight:**

If the webhook listener stopped, restart it:

```powershell
cd c:\Users\shash\Downloads\zoom+tracker

# Start ngrok first (in a new terminal)
.\ngrok http 5000

# Then start the listener (in another terminal)
python zoom_webhook_listener.py
```

---

## **📝 Expected Report Format:**

| Participant | Room | Joined | Left | Duration | Camera ON | Camera OFF |
|-------------|------|--------|------|----------|-----------|------------|
| Shweta G. | Room 3 | 15:38 | 15:57 | 19m | 15m | 4m |
| Aditya A. | Room 4 | 15:28 | 15:38 | 10m | 8m | 2m |

---

## **Credentials (Already Configured)**

- Account ID: `xhKbAsmnSM6pNYYYurmqIA`
- Client ID: `2ysNg6WLS0Sm8bKVVDeXcQ`
- Webhook URL: `https://[your-ngrok-url]/webhook`

---

## **Contact**
Created for Verve Advisory Virtual Office tracking.
