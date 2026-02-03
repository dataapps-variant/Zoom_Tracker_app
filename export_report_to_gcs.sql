-- ==============================================================================
-- EXPORT DAILY REPORT TO GCS AS CSV
-- ==============================================================================
--
-- WHAT THIS DOES:
-- Exports the daily_reports table to a CSV file in GCS bucket
--
-- OUTPUT:
-- gs://your-bucket/reports/DAILY_REPORT_2026-02-03.csv
--
-- HOW TO USE:
-- 1. Replace 'your-project-id' with your project
-- 2. Replace 'your-bucket' with your GCS bucket
-- 3. Run in BigQuery Console
--
-- TO SCHEDULE:
-- Create as scheduled query, run daily after the report generation
-- ==============================================================================

DECLARE target_date DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY);

EXPORT DATA OPTIONS(
  uri = CONCAT('gs://your-bucket/reports/DAILY_REPORT_', CAST(target_date AS STRING), '_*.csv'),
  format = 'CSV',
  overwrite = true,
  header = true,
  field_delimiter = ','
) AS

SELECT
  -- Date
  report_date AS `Report Date`,

  -- Participant Info
  participant_name AS `Participant Name`,
  participant_email AS `Email ID`,

  -- Meeting Times
  FORMAT_TIMESTAMP('%H:%M:%S', meeting_join_time) AS `Meeting Join Time`,
  FORMAT_TIMESTAMP('%H:%M:%S', meeting_leave_time) AS `Meeting Left Time`,
  meeting_duration_mins AS `Meeting Duration (mins)`,

  -- Room Info
  room_name AS `Room Name`,
  FORMAT_TIMESTAMP('%H:%M:%S', room_join_time) AS `Room Join Time`,
  FORMAT_TIMESTAMP('%H:%M:%S', room_leave_time) AS `Room Left Time`,
  room_duration_mins AS `Room Duration (mins)`,

  -- Camera Data
  camera_on_mins AS `Camera ON (mins)`,
  camera_off_mins AS `Camera OFF (mins)`,
  CONCAT(CAST(camera_percentage AS STRING), '%') AS `Camera %`,

  -- Journey
  next_room AS `Next Room`

FROM `your-project-id.zoom_tracker.daily_reports`
WHERE report_date = target_date
ORDER BY participant_name, room_number;
