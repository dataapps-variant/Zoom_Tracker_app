-- Daily Attendance Report with IST Times
-- One row per participant with room history
-- Format: Room Name [Join-Leave Duration]
-- All times in IST (UTC + 5:30)

-- Replace '2026-02-18' with your desired date

WITH participant_main AS (
  SELECT
    participant_email,
    participant_name,
    MIN(CASE WHEN event_type = 'participant_joined' THEN event_timestamp END) as joined_utc,
    MAX(CASE WHEN event_type = 'participant_left' THEN event_timestamp END) as left_utc
  FROM `variant-finance-data-project.breakout_room_calibrator.participant_events`
  WHERE event_date = '2026-02-18'
  GROUP BY participant_email, participant_name
),

room_joins AS (
  SELECT
    pe.participant_email,
    pe.participant_name,
    COALESCE(rm.room_name, pe.room_name) as room_name,
    pe.event_timestamp as join_time,
    pe.room_uuid
  FROM `variant-finance-data-project.breakout_room_calibrator.participant_events` pe
  LEFT JOIN `variant-finance-data-project.breakout_room_calibrator.room_mappings` rm
    ON pe.room_uuid LIKE CONCAT(SUBSTR(rm.room_uuid, 1, 8), '%')
    AND rm.source = 'webhook_calibration'
    AND rm.mapping_date = pe.event_date
  WHERE pe.event_date = '2026-02-18'
    AND pe.event_type = 'breakout_room_joined'
),

room_leaves AS (
  SELECT
    participant_email,
    participant_name,
    room_uuid,
    MIN(event_timestamp) as leave_time
  FROM `variant-finance-data-project.breakout_room_calibrator.participant_events`
  WHERE event_date = '2026-02-18'
    AND event_type = 'breakout_room_left'
  GROUP BY participant_email, participant_name, room_uuid
),

camera_on AS (
  SELECT
    participant_email,
    participant_name,
    MIN(event_timestamp) as cam_on_time
  FROM `variant-finance-data-project.breakout_room_calibrator.camera_events`
  WHERE event_date = '2026-02-18' AND camera_on = true
  GROUP BY participant_email, participant_name
),

camera_off AS (
  SELECT
    participant_email,
    participant_name,
    MAX(event_timestamp) as cam_off_time
  FROM `variant-finance-data-project.breakout_room_calibrator.camera_events`
  WHERE event_date = '2026-02-18' AND camera_on = false
  GROUP BY participant_email, participant_name
),

room_visits AS (
  SELECT
    rj.participant_email,
    rj.participant_name,
    rj.room_name,
    -- Convert to IST (UTC + 5:30 = 330 minutes)
    SUBSTR(CAST(TIMESTAMP_ADD(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', rj.join_time), INTERVAL 330 MINUTE) AS STRING), 12, 5) as join_ist,
    SUBSTR(CAST(TIMESTAMP_ADD(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', rl.leave_time), INTERVAL 330 MINUTE) AS STRING), 12, 5) as leave_ist,
    ROUND(TIMESTAMP_DIFF(
      PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', rl.leave_time),
      PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', rj.join_time),
      MINUTE
    ), 0) as duration_mins,
    rj.join_time as sort_time
  FROM room_joins rj
  LEFT JOIN room_leaves rl
    ON rj.participant_email = rl.participant_email
    AND rj.participant_name = rl.participant_name
    AND rj.room_uuid = rl.room_uuid
    AND rl.leave_time > rj.join_time
),

room_history AS (
  SELECT
    participant_email,
    participant_name,
    STRING_AGG(
      CONCAT(room_name, ' [', COALESCE(join_ist,'?'), '-', COALESCE(leave_ist,'?'), ' ', CAST(COALESCE(duration_mins,0) AS STRING), 'min]'),
      ' | ' ORDER BY sort_time
    ) as rooms
  FROM room_visits
  GROUP BY participant_email, participant_name
)

SELECT
  pm.participant_name as Name,
  pm.participant_email as Email,
  -- Main room times in IST
  SUBSTR(CAST(TIMESTAMP_ADD(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', pm.joined_utc), INTERVAL 330 MINUTE) AS STRING), 12, 5) as Main_Joined_IST,
  SUBSTR(CAST(TIMESTAMP_ADD(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', pm.left_utc), INTERVAL 330 MINUTE) AS STRING), 12, 5) as Main_Left_IST,
  -- Camera times in IST
  SUBSTR(CAST(TIMESTAMP_ADD(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', con.cam_on_time), INTERVAL 330 MINUTE) AS STRING), 12, 5) as Camera_On_IST,
  SUBSTR(CAST(TIMESTAMP_ADD(PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', coff.cam_off_time), INTERVAL 330 MINUTE) AS STRING), 12, 5) as Camera_Off_IST,
  -- Room history
  COALESCE(rh.rooms, '-') as Room_History
FROM participant_main pm
LEFT JOIN room_history rh
  ON pm.participant_email = rh.participant_email
  AND pm.participant_name = rh.participant_name
LEFT JOIN camera_on con
  ON pm.participant_email = con.participant_email
  AND pm.participant_name = con.participant_name
LEFT JOIN camera_off coff
  ON pm.participant_email = coff.participant_email
  AND pm.participant_name = coff.participant_name
WHERE pm.participant_name NOT LIKE '%Scout%'
ORDER BY pm.participant_name;
