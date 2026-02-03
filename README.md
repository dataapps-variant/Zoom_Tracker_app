# Zoom Tracker - GCP Cloud Run + GCS + BigQuery

Complete solution to track Zoom breakout room participation with camera data.

## Architecture

```
ZOOM MEETING
     │
     │ Webhook (join/leave events)
     ▼
┌─────────────────────────────────────────────────────────────────┐
│                      CLOUD RUN                                  │
│                  (zoom_webhook_bigquery.py)                     │
│                      Runs 24/7                                  │
└───────────────────────┬─────────────────┬───────────────────────┘
                        │                 │
          Write raw JSON│                 │Stream events
                        ▼                 ▼
┌───────────────────────────┐   ┌─────────────────────────────────┐
│         GCS BUCKET        │   │          BIGQUERY               │
│   /raw/2026-02-03/*.json  │   │        raw_events               │
│                           │   │                                 │
│   WHY: Cheap backup       │   │   WHY: Fast queries             │
│   Original JSON forever   │   │   Immediate availability        │
└───────────────────────────┘   └─────────────┬───────────────────┘
                                              │
                                              │ Scheduled Query (Daily 11PM)
                                              ▼
                                ┌─────────────────────────────────┐
                                │          BIGQUERY               │
                                │        daily_reports            │
                                │                                 │
                                │   Transformed report data       │
                                └─────────────┬───────────────────┘
                                              │
                                              │ Export Query
                                              ▼
                                ┌─────────────────────────────────┐
                                │         GCS BUCKET              │
                                │   /reports/DAILY_2026-02-03.csv │
                                │                                 │
                                │   WHY: Easy download/share      │
                                └─────────────────────────────────┘
```

---

## Files Overview

| File | Purpose |
|------|---------|
| `zoom_webhook_bigquery.py` | Webhook server → writes to GCS + BigQuery |
| `Dockerfile` | Container config for Cloud Run |
| `requirements.txt` | Python dependencies |
| `bigquery_setup.sql` | Create BigQuery tables |
| `bigquery_daily_report.sql` | Scheduled query for reports |
| `export_report_to_gcs.sql` | Export CSV to GCS |
| `load_gcs_to_bigquery.py` | Batch load from GCS (if needed) |
| `update_camera_data.py` | Add camera data from Zoom QOS API |

---

## Data Fields

### raw_events (Webhook Data)

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | STRING | Unique event ID (UUID) |
| `event_date` | DATE | Date (YYYY-MM-DD) for filtering |
| `event_type` | STRING | Zoom event name |
| `event_timestamp` | TIMESTAMP | Full timestamp |
| `meeting_id` | STRING | Zoom meeting ID |
| `meeting_uuid` | STRING | Unique meeting instance |
| `participant_id` | STRING | Zoom user ID |
| `participant_name` | STRING | Display name |
| `participant_email` | STRING | Email (if logged in) |
| `breakout_room_uuid` | STRING | Which breakout room |
| `action` | STRING | JOIN or LEAVE |
| `raw_payload` | STRING | Original JSON |
| `gcs_path` | STRING | GCS file location |
| `inserted_at` | TIMESTAMP | When stored |

### daily_reports (Final Report)

| Field | Type | Description |
|-------|------|-------------|
| `report_date` | DATE | Report date |
| `participant_name` | STRING | Display name |
| `participant_email` | STRING | Email ID |
| `meeting_join_time` | TIMESTAMP | First join |
| `meeting_leave_time` | TIMESTAMP | Last leave |
| `meeting_duration_mins` | FLOAT | Total meeting time |
| `room_number` | INT | Visit order (1, 2, 3...) |
| `room_name` | STRING | Room-1, Room-2... |
| `room_uuid` | STRING | Room identifier |
| `room_join_time` | TIMESTAMP | Entered room |
| `room_leave_time` | TIMESTAMP | Left room |
| `room_duration_mins` | FLOAT | Time in room |
| `camera_on_mins` | FLOAT | Camera ON time |
| `camera_off_mins` | FLOAT | Camera OFF time |
| `camera_percentage` | FLOAT | Camera ON % |
| `next_room` | STRING | Next destination |

---

## Setup Steps

### Step 1: Create GCS Bucket

```bash
# Create bucket
gsutil mb -l US gs://zoom-tracker-data

# Create folders
gsutil cp /dev/null gs://zoom-tracker-data/raw/.keep
gsutil cp /dev/null gs://zoom-tracker-data/reports/.keep
```

### Step 2: Create BigQuery Dataset & Tables

1. Go to [BigQuery Console](https://console.cloud.google.com/bigquery)
2. Open `bigquery_setup.sql`
3. Replace `your-project-id` with your project ID
4. Run each CREATE statement

### Step 3: Deploy to Cloud Run

```bash
cd gcp

# Login
gcloud auth login

# Set project
gcloud config set project YOUR_PROJECT_ID

# Deploy
gcloud run deploy zoom-webhook \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "ZOOM_WEBHOOK_SECRET=r72xUnMLTHOgHcgZS3Np7Q,GCP_PROJECT_ID=YOUR_PROJECT_ID,GCS_BUCKET=zoom-tracker-data,BQ_DATASET=zoom_tracker,BQ_TABLE=raw_events"
```

### Step 4: Update Zoom Webhook URL

1. Go to [Zoom Marketplace](https://marketplace.zoom.us/)
2. Open your app → Features → Event Subscriptions
3. Set webhook URL: `https://YOUR-CLOUD-RUN-URL/webhook`
4. Save

### Step 5: Create Scheduled Queries

**Daily Report (11:00 PM):**
1. BigQuery Console → Scheduled Queries → Create
2. Paste `bigquery_daily_report.sql` content
3. Replace `your-project-id`
4. Schedule: Daily at 23:00

**Export to GCS (11:30 PM):**
1. Create another scheduled query
2. Paste `export_report_to_gcs.sql` content
3. Schedule: Daily at 23:30

### Step 6: Update Camera Data (After Meetings)

```bash
# Set environment variables
export GCP_PROJECT_ID=your-project-id

# Run for specific date
python update_camera_data.py 2026-02-03
```

---

## Daily Workflow

| Time | What Happens | How |
|------|--------------|-----|
| 9:00 AM | Meeting starts | - |
| 9:00 AM+ | Webhook captures events | Automatic (Cloud Run) |
| 9:00 AM+ | Events saved to GCS + BigQuery | Automatic |
| 6:00 PM | Meeting ends | - |
| 8:00 PM | Camera data available | Zoom processes QOS |
| 8:00 PM | Run camera update script | Manual or Cloud Scheduler |
| 11:00 PM | Daily report generated | Scheduled Query |
| 11:30 PM | CSV exported to GCS | Scheduled Query |

---

## Query Examples

### Get today's events
```sql
SELECT * FROM `project.zoom_tracker.raw_events`
WHERE event_date = CURRENT_DATE()
ORDER BY event_timestamp;
```

### Get participant journey
```sql
SELECT
  report_date,
  participant_name,
  room_name,
  room_join_time,
  room_leave_time,
  room_duration_mins,
  camera_percentage,
  next_room
FROM `project.zoom_tracker.daily_reports`
WHERE report_date = '2026-02-03'
ORDER BY participant_name, room_number;
```

### Room activity summary
```sql
SELECT
  room_name,
  COUNT(DISTINCT participant_name) as visitors,
  AVG(room_duration_mins) as avg_time,
  AVG(camera_percentage) as avg_camera
FROM `project.zoom_tracker.daily_reports`
WHERE report_date = '2026-02-03'
GROUP BY room_name
ORDER BY room_name;
```

---

## GCS Structure

```
gs://zoom-tracker-data/
│
├── raw/                          # Raw webhook JSON
│   ├── 2026-02-03/
│   │   ├── uuid1.json
│   │   ├── uuid2.json
│   │   └── ...
│   └── 2026-02-04/
│       └── ...
│
└── reports/                      # Exported CSV reports
    ├── DAILY_REPORT_2026-02-03_000000000000.csv
    └── DAILY_REPORT_2026-02-04_000000000000.csv
```

---

## Cost Estimate

| Service | Free Tier | Usage | Est. Cost |
|---------|-----------|-------|-----------|
| Cloud Run | 2M requests/mo | ~1000/day | $0 |
| GCS | 5GB | ~100MB/mo | $0 |
| BigQuery Storage | 10GB | ~500MB/mo | $0 |
| BigQuery Queries | 1TB/mo | ~1GB/mo | $0 |
| **Total** | | | **$0/month** |

---

## Troubleshooting

**Webhook not receiving?**
```bash
# Check Cloud Run logs
gcloud run services logs read zoom-webhook --limit=50

# Test endpoint
curl https://YOUR-URL/
curl https://YOUR-URL/test-gcs
curl https://YOUR-URL/test-bq
```

**GCS permission denied?**
- Ensure Cloud Run service account has `Storage Object Admin` role

**BigQuery insert failed?**
- Ensure service account has `BigQuery Data Editor` role
- Check table exists with correct schema

**Camera data not appearing?**
- QOS available ~2-3 hours after meeting
- Check Zoom API credentials
- Run: `python update_camera_data.py DATE`
