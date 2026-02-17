-- ============================================================================
-- BIGQUERY SCHEMA DEFINITIONS
-- Zoom Breakout Room Tracker
-- ============================================================================
-- Run these in BigQuery Console or via bq command

-- Create Dataset (run once)
-- bq mk --dataset breakout_room_calibrator

-- ============================================================================
-- TABLE 1: participant_events
-- Stores all join/leave events for participants
-- ============================================================================
CREATE TABLE IF NOT EXISTS `breakout_room_calibrator.participant_events` (
    event_id STRING NOT NULL,
    event_type STRING NOT NULL,
    -- Values: participant_joined, participant_left,
    --         breakout_room_joined, breakout_room_left
    event_timestamp STRING NOT NULL,
    event_date DATE NOT NULL,
    meeting_id STRING NOT NULL,
    meeting_uuid STRING,
    participant_id STRING NOT NULL,
    participant_name STRING NOT NULL,
    participant_email STRING,
    room_uuid STRING,
    room_name STRING,
    inserted_at STRING NOT NULL
)
PARTITION BY event_date
CLUSTER BY participant_id, event_type
OPTIONS (
    description = 'All participant join/leave events with room tracking',
    labels = [("app", "breakout_room_calibrator")]
);


-- ============================================================================
-- TABLE 2: camera_events
-- Stores camera ON/OFF events with exact timestamps
-- ============================================================================
CREATE TABLE IF NOT EXISTS `breakout_room_calibrator.camera_events` (
    event_id STRING NOT NULL,
    event_type STRING NOT NULL,
    -- Values: camera_on, camera_off
    event_timestamp STRING NOT NULL,
    event_date DATE NOT NULL,
    event_time STRING NOT NULL,
    -- Exact time HH:MM:SS
    meeting_id STRING NOT NULL,
    meeting_uuid STRING,
    participant_id STRING NOT NULL,
    participant_name STRING NOT NULL,
    participant_email STRING,
    camera_on BOOL NOT NULL,
    room_name STRING,
    duration_seconds INT64,
    -- Duration camera was ON (calculated when turning OFF)
    inserted_at STRING NOT NULL
)
PARTITION BY event_date
CLUSTER BY participant_id, camera_on
OPTIONS (
    description = 'Camera ON/OFF events with exact timestamps',
    labels = [("app", "breakout_room_calibrator")]
);


-- ============================================================================
-- TABLE 3: room_mappings
-- Dynamic room mappings (refreshed each meeting)
-- ============================================================================
CREATE TABLE IF NOT EXISTS `breakout_room_calibrator.room_mappings` (
    mapping_id STRING NOT NULL,
    meeting_id STRING NOT NULL,
    meeting_uuid STRING,
    room_uuid STRING NOT NULL,
    room_name STRING NOT NULL,
    room_index INT64,
    mapping_date DATE NOT NULL,
    mapped_at STRING NOT NULL,
    source STRING DEFAULT 'zoom_sdk_app'
    -- Values: zoom_sdk_app, manual, api
)
PARTITION BY mapping_date
CLUSTER BY meeting_id
OPTIONS (
    description = 'Dynamic room UUID to name mappings per meeting',
    labels = [("app", "breakout_room_calibrator")]
);


-- ============================================================================
-- TABLE 4: qos_data
-- Quality of Service metrics
-- ============================================================================
CREATE TABLE IF NOT EXISTS `breakout_room_calibrator.qos_data` (
    qos_id STRING NOT NULL,
    meeting_uuid STRING NOT NULL,
    participant_id STRING,
    participant_name STRING,
    participant_email STRING,
    join_time STRING,
    leave_time STRING,
    duration_minutes INT64,
    attentiveness_score STRING,
    recorded_at STRING NOT NULL,
    event_date DATE NOT NULL
)
PARTITION BY event_date
CLUSTER BY participant_id
OPTIONS (
    description = 'Quality of Service data from Zoom API',
    labels = [("app", "breakout_room_calibrator")]
);


-- ============================================================================
-- USEFUL QUERIES FOR REPORTS
-- ============================================================================

-- Query 1: Daily Attendance Summary
-- SELECT
--     participant_name,
--     participant_email,
--     MIN(event_timestamp) as first_join,
--     MAX(event_timestamp) as last_activity,
--     COUNT(DISTINCT CASE WHEN event_type = 'participant_joined' THEN event_timestamp END) as join_count,
--     STRING_AGG(DISTINCT room_name, ', ') as rooms_visited
-- FROM `breakout_room_calibrator.participant_events`
-- WHERE event_date = '2026-02-16'
-- GROUP BY participant_name, participant_email
-- ORDER BY participant_name;


-- Query 2: Camera Duration per Participant
-- SELECT
--     participant_name,
--     SUM(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds ELSE 0 END) as total_camera_on_seconds,
--     ROUND(SUM(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds ELSE 0 END) / 60.0, 2) as total_camera_on_minutes,
--     COUNT(CASE WHEN camera_on = TRUE THEN 1 END) as camera_on_count,
--     COUNT(CASE WHEN camera_on = FALSE THEN 1 END) as camera_off_count
-- FROM `breakout_room_calibrator.camera_events`
-- WHERE event_date = '2026-02-16'
-- GROUP BY participant_name
-- ORDER BY participant_name;


-- Query 3: Room Visit History
-- SELECT
--     participant_name,
--     room_name,
--     MIN(event_timestamp) as entered_at,
--     MAX(event_timestamp) as left_at
-- FROM `breakout_room_calibrator.participant_events`
-- WHERE event_date = '2026-02-16'
--     AND room_name IS NOT NULL
--     AND room_name != ''
-- GROUP BY participant_name, room_name
-- ORDER BY participant_name, entered_at;


-- Query 4: Delete Old Mappings (run at start of each day)
-- DELETE FROM `breakout_room_calibrator.room_mappings`
-- WHERE mapping_date = CURRENT_DATE();
