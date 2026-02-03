-- ==============================================================================
-- BIGQUERY SETUP - CREATE DATASET AND TABLES
-- ==============================================================================
-- Run these in BigQuery Console: https://console.cloud.google.com/bigquery
-- Replace 'your-project-id' with your actual GCP project ID

-- ==============================================================================
-- STEP 1: CREATE DATASET
-- ==============================================================================
CREATE SCHEMA IF NOT EXISTS `your-project-id.zoom_tracker`
OPTIONS (
  description = 'Zoom breakout room tracking data',
  location = 'US'
);

-- ==============================================================================
-- STEP 2: CREATE RAW EVENTS TABLE
-- ==============================================================================
-- Stores every webhook event from Zoom
--
-- FIELDS EXPLAINED:
-- event_id        : Unique ID (UUID) we generate
-- event_date      : Date only (YYYY-MM-DD) for easy filtering by day
-- event_type      : Zoom event name (joined/left breakout room)
-- event_timestamp : Full timestamp with time
-- meeting_id      : Zoom meeting ID (same for recurring meetings)
-- meeting_uuid    : Unique per meeting instance
-- participant_*   : Who joined/left
-- breakout_room_uuid : Which room
-- action          : Simplified JOIN or LEAVE
-- raw_payload     : Original JSON for debugging
-- gcs_path        : Where raw file stored in GCS

CREATE TABLE IF NOT EXISTS `your-project-id.zoom_tracker.raw_events` (
  event_id STRING NOT NULL,
  event_date DATE,                    -- NEW: For easy date filtering
  event_type STRING,
  event_timestamp TIMESTAMP,
  meeting_id STRING,
  meeting_uuid STRING,
  participant_id STRING,
  participant_name STRING,
  participant_email STRING,
  breakout_room_uuid STRING,
  action STRING,
  raw_payload STRING,
  inserted_at TIMESTAMP,
  gcs_path STRING                     -- NEW: Reference to GCS file
)
PARTITION BY event_date              -- Partition by date for fast queries
CLUSTER BY participant_name, action; -- Cluster for common query patterns

-- ==============================================================================
-- STEP 3: CREATE DAILY REPORTS TABLE
-- ==============================================================================
-- Transformed report data with all fields user requested
--
-- FIELDS:
-- report_date         : The date of this report
-- participant_name    : Display name
-- participant_email   : Email ID
-- meeting_join_time   : When they joined meeting
-- meeting_leave_time  : When they left meeting
-- meeting_duration    : Total time in meeting (minutes)
-- room_number         : Which room visit (1st, 2nd, 3rd...)
-- room_name           : Room-1, Room-2, etc.
-- room_uuid           : Actual room UUID
-- room_join_time      : Entered this room
-- room_leave_time     : Left this room
-- room_duration       : Time in this room (minutes)
-- camera_on_mins      : Minutes with camera ON
-- camera_off_mins     : Minutes with camera OFF
-- camera_percentage   : % of time camera was ON
-- next_room           : Where they went next

CREATE TABLE IF NOT EXISTS `your-project-id.zoom_tracker.daily_reports` (
  -- Date
  report_date DATE NOT NULL,

  -- Participant Info
  participant_name STRING,
  participant_email STRING,

  -- Meeting Level
  meeting_join_time TIMESTAMP,
  meeting_leave_time TIMESTAMP,
  meeting_duration_mins FLOAT64,

  -- Room Level
  room_number INT64,
  room_name STRING,                   -- NEW: Room-1, Room-2, etc.
  room_uuid STRING,
  room_join_time TIMESTAMP,
  room_leave_time TIMESTAMP,
  room_duration_mins FLOAT64,

  -- Camera Data
  camera_on_mins FLOAT64,
  camera_off_mins FLOAT64,
  camera_percentage FLOAT64,

  -- Journey
  next_room STRING,

  -- Metadata
  created_at TIMESTAMP
)
PARTITION BY report_date
CLUSTER BY participant_name;

-- ==============================================================================
-- STEP 4: CREATE PARTICIPANT SUMMARY VIEW
-- ==============================================================================
-- Quick view of each participant's activity per day

CREATE OR REPLACE VIEW `your-project-id.zoom_tracker.participant_summary` AS
SELECT
  report_date,
  participant_name,
  participant_email,
  meeting_join_time,
  meeting_leave_time,
  meeting_duration_mins,
  COUNT(*) as rooms_visited,
  SUM(room_duration_mins) as total_room_time_mins,
  AVG(camera_percentage) as avg_camera_percentage
FROM `your-project-id.zoom_tracker.daily_reports`
GROUP BY 1, 2, 3, 4, 5, 6
ORDER BY report_date DESC, participant_name;

-- ==============================================================================
-- STEP 5: CREATE ROOM ACTIVITY VIEW
-- ==============================================================================
-- See activity per room per day

CREATE OR REPLACE VIEW `your-project-id.zoom_tracker.room_activity` AS
SELECT
  report_date,
  room_name,
  room_uuid,
  COUNT(DISTINCT participant_name) as unique_participants,
  COUNT(*) as total_visits,
  AVG(room_duration_mins) as avg_time_in_room_mins,
  AVG(camera_percentage) as avg_camera_percentage
FROM `your-project-id.zoom_tracker.daily_reports`
GROUP BY 1, 2, 3
ORDER BY report_date DESC, room_name;
