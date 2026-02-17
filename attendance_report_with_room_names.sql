-- ============================================================================
-- ATTENDANCE REPORT WITH PROPER ROOM NAMES
-- ============================================================================
-- This query resolves the UUID format mismatch between SDK and Webhooks
-- by using room_mappings table which stores both SDK and webhook UUIDs
--
-- Run this in BigQuery Console:
-- https://console.cloud.google.com/bigquery

-- First, check what mappings we have (run this separately to debug)
-- SELECT DISTINCT room_uuid, room_name, source, mapping_date
-- FROM `variant-finance-data-project.breakout_room_calibrator.room_mappings`
-- WHERE mapping_date = CURRENT_DATE()
-- ORDER BY source, room_name;

-- ============================================================================
-- MAIN REPORT: Participant attendance with resolved room names
-- ============================================================================

WITH
-- Get all room mappings for today
all_mappings AS (
    SELECT
        room_uuid,
        room_name,
        source,
        -- Create additional lookup keys
        SUBSTR(room_uuid, 1, 8) as short_uuid,
        LOWER(room_uuid) as lower_uuid
    FROM `variant-finance-data-project.breakout_room_calibrator.room_mappings`
    WHERE mapping_date = CURRENT_DATE()
    AND room_name IS NOT NULL
    AND room_name != ''
),

-- Enrich events with resolved room names
events_enriched AS (
    SELECT
        pe.event_id,
        pe.event_type,
        pe.event_timestamp,
        pe.event_date,
        pe.meeting_id,
        pe.participant_id,
        pe.participant_name,
        pe.participant_email,
        pe.room_uuid,
        pe.room_name as original_room_name,
        -- Resolve room name using multiple matching strategies
        COALESCE(
            -- 1. If already has proper room name (not Room-xxx pattern)
            CASE WHEN pe.room_name IS NOT NULL
                 AND pe.room_name != ''
                 AND NOT STARTS_WITH(pe.room_name, 'Room-')
                 THEN pe.room_name
            END,
            -- 2. Exact UUID match
            (SELECT m.room_name FROM all_mappings m WHERE m.room_uuid = pe.room_uuid LIMIT 1),
            -- 3. Match by short UUID (first 8 chars)
            (SELECT m.room_name FROM all_mappings m WHERE m.short_uuid = SUBSTR(pe.room_uuid, 1, 8) LIMIT 1),
            -- 4. Case-insensitive match
            (SELECT m.room_name FROM all_mappings m WHERE m.lower_uuid = LOWER(pe.room_uuid) LIMIT 1),
            -- 5. Fallback to original or 'Unknown Room'
            CASE WHEN pe.room_name IS NOT NULL AND pe.room_name != '' THEN pe.room_name
                 WHEN pe.room_uuid IS NOT NULL AND pe.room_uuid != '' THEN CONCAT('Room-', SUBSTR(pe.room_uuid, 1, 8))
                 ELSE 'Main Room'
            END
        ) as resolved_room_name,
        pe.inserted_at
    FROM `variant-finance-data-project.breakout_room_calibrator.participant_events` pe
    WHERE pe.event_date = CURRENT_DATE()
)

-- Final attendance summary
SELECT
    participant_name,
    participant_email,
    FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', TIMESTAMP(MIN(event_timestamp))) as first_join,
    FORMAT_TIMESTAMP('%Y-%m-%d %H:%M:%S', TIMESTAMP(MAX(event_timestamp))) as last_activity,
    COUNT(CASE WHEN event_type = 'participant_joined' THEN 1 END) as join_count,
    COUNT(CASE WHEN event_type = 'breakout_room_joined' THEN 1 END) as room_visits,
    STRING_AGG(
        DISTINCT CASE
            WHEN resolved_room_name != ''
            AND resolved_room_name IS NOT NULL
            AND resolved_room_name != 'Main Room'
            THEN resolved_room_name
        END,
        ', '
        ORDER BY resolved_room_name
    ) as rooms_visited
FROM events_enriched
GROUP BY participant_name, participant_email
ORDER BY participant_name;
