-- ==============================================================================
-- BIGQUERY SCHEDULED QUERY - GENERATE DAILY REPORT
-- ==============================================================================
--
-- WHAT THIS DOES:
-- 1. Takes raw webhook events for a specific date
-- 2. Matches JOIN and LEAVE events for each participant-room
-- 3. Calculates durations and room order
-- 4. Creates the final report with all fields
--
-- OUTPUT FIELDS:
-- - report_date: The date
-- - participant_name: Display name
-- - participant_email: Email ID
-- - meeting_join_time: First room join
-- - meeting_leave_time: Last room leave
-- - meeting_duration_mins: Total meeting time
-- - room_number: 1, 2, 3... (order visited)
-- - room_name: Room-1, Room-2, Room-3...
-- - room_uuid: Actual UUID
-- - room_join_time: Entered room
-- - room_leave_time: Left room
-- - room_duration_mins: Time in room
-- - camera_on_mins: Camera ON time
-- - camera_off_mins: Camera OFF time
-- - camera_percentage: Camera %
-- - next_room: Where they went next
--
-- HOW TO SCHEDULE:
-- 1. BigQuery Console → Scheduled Queries → Create
-- 2. Paste this query
-- 3. Schedule: Daily at 11:00 PM
-- ==============================================================================

-- Set the report date (yesterday by default)
DECLARE target_date DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY);

-- Delete existing data for this date (allow re-runs)
DELETE FROM `your-project-id.zoom_tracker.daily_reports`
WHERE report_date = target_date;

-- Generate report
INSERT INTO `your-project-id.zoom_tracker.daily_reports`
(
  report_date,
  participant_name,
  participant_email,
  meeting_join_time,
  meeting_leave_time,
  meeting_duration_mins,
  room_number,
  room_name,
  room_uuid,
  room_join_time,
  room_leave_time,
  room_duration_mins,
  camera_on_mins,
  camera_off_mins,
  camera_percentage,
  next_room,
  created_at
)

WITH
-- ==============================================================================
-- STEP 1: Get all JOIN events with room numbers
-- ==============================================================================
joins AS (
  SELECT
    event_date,
    participant_name,
    COALESCE(participant_email, '') as participant_email,
    breakout_room_uuid,
    event_timestamp as join_time,
    -- Number rooms in order of visit
    ROW_NUMBER() OVER (
      PARTITION BY participant_name
      ORDER BY event_timestamp
    ) as room_number
  FROM `your-project-id.zoom_tracker.raw_events`
  WHERE event_date = target_date
    AND action = 'JOIN'
    AND breakout_room_uuid IS NOT NULL
    AND breakout_room_uuid != ''
),

-- ==============================================================================
-- STEP 2: Get all LEAVE events
-- ==============================================================================
leaves AS (
  SELECT
    participant_name,
    breakout_room_uuid,
    event_timestamp as leave_time
  FROM `your-project-id.zoom_tracker.raw_events`
  WHERE event_date = target_date
    AND action = 'LEAVE'
),

-- ==============================================================================
-- STEP 3: Match JOINs with LEAVEs
-- ==============================================================================
room_visits AS (
  SELECT
    j.event_date,
    j.participant_name,
    j.participant_email,
    j.breakout_room_uuid as room_uuid,
    j.join_time as room_join_time,
    j.room_number,
    -- Find matching LEAVE (first LEAVE after this JOIN for same room)
    (
      SELECT MIN(l.leave_time)
      FROM leaves l
      WHERE l.participant_name = j.participant_name
        AND l.breakout_room_uuid = j.breakout_room_uuid
        AND l.leave_time > j.join_time
    ) as room_leave_time
  FROM joins j
),

-- ==============================================================================
-- STEP 4: Calculate meeting-level times
-- ==============================================================================
meeting_times AS (
  SELECT
    participant_name,
    MIN(room_join_time) as meeting_join_time,
    MAX(COALESCE(room_leave_time, room_join_time)) as meeting_leave_time
  FROM room_visits
  GROUP BY participant_name
),

-- ==============================================================================
-- STEP 5: Add next room info
-- ==============================================================================
with_next AS (
  SELECT
    rv.*,
    -- Get next room number
    LEAD(room_number) OVER (
      PARTITION BY rv.participant_name
      ORDER BY room_number
    ) as next_room_number
  FROM room_visits rv
)

-- ==============================================================================
-- FINAL SELECT
-- ==============================================================================
SELECT
  wn.event_date as report_date,
  wn.participant_name,
  wn.participant_email,
  mt.meeting_join_time,
  mt.meeting_leave_time,
  ROUND(TIMESTAMP_DIFF(mt.meeting_leave_time, mt.meeting_join_time, SECOND) / 60.0, 1) as meeting_duration_mins,
  wn.room_number,
  CONCAT('Room-', CAST(wn.room_number AS STRING)) as room_name,
  wn.room_uuid,
  wn.room_join_time,
  wn.room_leave_time,
  CASE
    WHEN wn.room_leave_time IS NOT NULL
    THEN ROUND(TIMESTAMP_DIFF(wn.room_leave_time, wn.room_join_time, SECOND) / 60.0, 1)
    ELSE 0
  END as room_duration_mins,
  -- Camera data (placeholder - updated by separate process)
  0 as camera_on_mins,
  0 as camera_off_mins,
  0 as camera_percentage,
  -- Next room
  CASE
    WHEN wn.next_room_number IS NOT NULL
    THEN CONCAT('Room-', CAST(wn.next_room_number AS STRING))
    ELSE 'Left Meeting'
  END as next_room,
  CURRENT_TIMESTAMP() as created_at

FROM with_next wn
JOIN meeting_times mt ON wn.participant_name = mt.participant_name
ORDER BY wn.participant_name, wn.room_number;


-- ==============================================================================
-- VERIFY: Show what was inserted
-- ==============================================================================
-- SELECT * FROM `your-project-id.zoom_tracker.daily_reports`
-- WHERE report_date = target_date
-- ORDER BY participant_name, room_number;
