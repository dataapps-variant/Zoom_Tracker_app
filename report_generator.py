"""
DAILY ATTENDANCE REPORT GENERATOR
=================================
Generates CSV report with ONE ROW PER PARTICIPANT
All times in IST (Indian Standard Time)

Format:
- Name, Email, Main Join IST, Main Left IST, Camera On, Camera Off
- Room History: RoomName [JoinTime-LeaveTime Duration] | NextRoom [...]

Triggered by Cloud Scheduler daily or /generate-report endpoint
"""

from google.cloud import bigquery
from datetime import datetime, timedelta
import os
import csv
import io
import json

# SendGrid for email
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition
    import base64
    SENDGRID_AVAILABLE = True
except ImportError:
    SENDGRID_AVAILABLE = False
    print("[ReportGenerator] SendGrid not installed - email disabled")

# Configuration
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'variant-finance-data-project')
BQ_DATASET = os.environ.get('BQ_DATASET', 'breakout_room_calibrator')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
REPORT_EMAIL_FROM = os.environ.get('REPORT_EMAIL_FROM', 'reports@verveadvisory.com')
REPORT_EMAIL_TO = os.environ.get('REPORT_EMAIL_TO', '')


def get_bq_client():
    """Get BigQuery client"""
    return bigquery.Client(project=GCP_PROJECT_ID)


def generate_daily_report(report_date=None):
    """
    Generate daily attendance report with ONE ROW PER PARTICIPANT
    All times in IST (UTC + 5:30)

    Args:
        report_date: Date string 'YYYY-MM-DD' (defaults to yesterday)

    Returns:
        Dictionary with report data and CSV content
    """
    if report_date is None:
        # Default to yesterday
        report_date = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"[Report] Generating report for {report_date}")

    client = get_bq_client()

    # =============================================
    # MAIN QUERY - ONE ROW PER PARTICIPANT
    # With room history, all times in IST
    # =============================================
    main_query = f"""
    WITH participant_main AS (
      SELECT
        participant_email,
        participant_name,
        MIN(CASE WHEN event_type = 'participant_joined' THEN event_timestamp END) as joined_utc,
        MAX(CASE WHEN event_type = 'participant_left' THEN event_timestamp END) as left_utc
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.participant_events`
      WHERE event_date = '{report_date}'
      GROUP BY participant_email, participant_name
    ),
    room_joins AS (
      SELECT
        pe.participant_email,
        pe.participant_name,
        COALESCE(rm.room_name, pe.room_name) as room_name,
        pe.event_timestamp as join_time,
        pe.room_uuid
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.participant_events` pe
      LEFT JOIN `{GCP_PROJECT_ID}.{BQ_DATASET}.room_mappings` rm
        ON pe.room_uuid LIKE CONCAT(SUBSTR(rm.room_uuid, 1, 8), '%')
        AND rm.source = 'webhook_calibration'
        AND rm.mapping_date = pe.event_date
      WHERE pe.event_date = '{report_date}'
        AND pe.event_type = 'breakout_room_joined'
    ),
    room_leaves AS (
      SELECT
        participant_email,
        participant_name,
        room_uuid,
        MIN(event_timestamp) as leave_time
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.participant_events`
      WHERE event_date = '{report_date}'
        AND event_type = 'breakout_room_left'
      GROUP BY participant_email, participant_name, room_uuid
    ),
    camera_on AS (
      SELECT
        participant_email,
        participant_name,
        MIN(event_timestamp) as cam_on_time
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.camera_events`
      WHERE event_date = '{report_date}' AND camera_on = true
      GROUP BY participant_email, participant_name
    ),
    camera_off AS (
      SELECT
        participant_email,
        participant_name,
        MAX(event_timestamp) as cam_off_time
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.camera_events`
      WHERE event_date = '{report_date}' AND camera_on = false
      GROUP BY participant_email, participant_name
    ),
    qos_camera AS (
      SELECT
        participant_name,
        participant_email,
        MAX(camera_on_count) as camera_on_intervals,
        MAX(duration_minutes) as qos_duration_min
      FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.qos_data`
      WHERE event_date = '{report_date}'
      GROUP BY participant_name, participant_email
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
      -- Total duration
      ROUND(TIMESTAMP_DIFF(
        PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', pm.left_utc),
        PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%E*S', pm.joined_utc),
        MINUTE
      ), 0) as Total_Duration_Min,
      -- QoS duration
      COALESCE(qc.qos_duration_min, 0) as QoS_Duration_Min,
      -- Camera ON intervals (from QoS API)
      COALESCE(qc.camera_on_intervals, 0) as Camera_On_Intervals,
      -- Room history
      COALESCE(rh.rooms, '-') as Room_History
    FROM participant_main pm
    LEFT JOIN room_history rh
      ON pm.participant_email = rh.participant_email
      AND pm.participant_name = rh.participant_name
    LEFT JOIN qos_camera qc
      ON pm.participant_name = qc.participant_name
    WHERE pm.participant_name NOT LIKE '%Scout%'
    ORDER BY pm.participant_name
    """

    try:
        results = list(client.query(main_query).result())
        print(f"[Report] Query returned {len(results)} participants")
    except Exception as e:
        print(f"[Report] Query error: {e}")
        results = []

    # =============================================
    # BUILD REPORT OBJECT
    # =============================================
    report = {
        'report_date': report_date,
        'generated_at': datetime.utcnow().isoformat(),
        'total_participants': len(results),
        'participants': [dict(row.items()) for row in results]
    }

    # Generate CSV
    report['csv_content'] = generate_csv(report)

    print(f"[Report] Generated report with {len(results)} participants")
    return report


def generate_csv(report):
    """Generate CSV content from report data"""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header - matches query output
    writer.writerow([
        'Name',
        'Email',
        'Main_Joined_IST',
        'Main_Left_IST',
        'Total_Duration_Min',
        'QoS_Duration_Min',
        'Camera_On_Intervals',
        'Room_History'
    ])

    # Data rows
    for p in report['participants']:
        writer.writerow([
            p.get('Name', '') or '',
            p.get('Email', '') or '',
            p.get('Main_Joined_IST', '') or '',
            p.get('Main_Left_IST', '') or '',
            p.get('Total_Duration_Min', '') or '',
            p.get('QoS_Duration_Min', 0) or 0,
            p.get('Camera_On_Intervals', 0) or 0,
            p.get('Room_History', '-') or '-'
        ])

    return output.getvalue()


def send_report_email(report, report_date):
    """Send report via SendGrid with CSV attachment"""
    if not SENDGRID_AVAILABLE:
        print("[Report] SendGrid not available")
        return False

    if not all([SENDGRID_API_KEY, REPORT_EMAIL_FROM, REPORT_EMAIL_TO]):
        print("[Report] Email configuration incomplete")
        print(f"  SENDGRID_API_KEY: {'set' if SENDGRID_API_KEY else 'NOT SET'}")
        print(f"  REPORT_EMAIL_FROM: {REPORT_EMAIL_FROM}")
        print(f"  REPORT_EMAIL_TO: {REPORT_EMAIL_TO}")
        return False

    try:
        # Build HTML email body
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ color: #2D8CFF; }}
                h2 {{ color: #333; border-bottom: 2px solid #2D8CFF; padding-bottom: 5px; }}
                table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 12px; }}
                th {{ background-color: #2D8CFF; color: white; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                .summary {{ background: #f0f8ff; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .footer {{ color: #666; font-size: 12px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; }}
            </style>
        </head>
        <body>
            <h1>Daily Zoom Attendance Report</h1>
            <p><strong>Date:</strong> {report_date}</p>
            <p><strong>Generated:</strong> {report['generated_at']} UTC</p>
            <p><strong>All times shown in IST (Indian Standard Time)</strong></p>

            <div class="summary">
                <h2>Summary</h2>
                <p><strong>Total Participants:</strong> {report['total_participants']}</p>
            </div>

            <h2>Attendance (First 30 shown, full data in CSV)</h2>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>Joined IST</th>
                    <th>Left IST</th>
                    <th>Duration</th>
                    <th>Camera ON</th>
                    <th>Room History</th>
                </tr>
        """

        for p in report['participants'][:30]:  # Limit to 30 in email
            room_history = p.get('Room_History', '-') or '-'
            # Truncate long room history for email
            if len(room_history) > 80:
                room_history = room_history[:80] + '...'

            camera_intervals = p.get('Camera_On_Intervals', 0) or 0

            html_content += f"""
                <tr>
                    <td>{p.get('Name', '')}</td>
                    <td>{p.get('Email', '')}</td>
                    <td>{p.get('Main_Joined_IST', '')}</td>
                    <td>{p.get('Main_Left_IST', '')}</td>
                    <td>{p.get('Total_Duration_Min', '')} min</td>
                    <td>{camera_intervals}</td>
                    <td style="font-size:10px;">{room_history}</td>
                </tr>
            """

        html_content += """
            </table>

            <div class="footer">
                <p><strong>Full attendance data is in the attached CSV file.</strong></p>
                <p>CSV Format: One row per participant with complete room visit history</p>
                <p>Room History Format: RoomName [JoinTime-LeaveTime Duration] | NextRoom [...]</p>
                <p>Generated by Zoom Breakout Room Tracker</p>
            </div>
        </body>
        </html>
        """

        # Create email
        message = Mail(
            from_email=REPORT_EMAIL_FROM,
            to_emails=REPORT_EMAIL_TO.split(','),
            subject=f"Daily Zoom Attendance Report - {report_date}",
            html_content=html_content
        )

        # Attach CSV
        csv_content = report['csv_content']
        encoded = base64.b64encode(csv_content.encode('utf-8')).decode()
        attachment = Attachment(
            FileContent(encoded),
            FileName(f"attendance_report_{report_date}.csv"),
            FileType('text/csv'),
            Disposition('attachment')
        )
        message.add_attachment(attachment)

        # Send
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)

        print(f"[Report] Email sent! Status: {response.status_code}")
        return True

    except Exception as e:
        print(f"[Report] Email error: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_csv_to_gcs(report, report_date, bucket_name):
    """Save CSV file to Google Cloud Storage"""
    from google.cloud import storage

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        blob_path = f"reports/attendance_report_{report_date}.csv"
        blob = bucket.blob(blob_path)
        blob.upload_from_string(report['csv_content'], content_type='text/csv')

        print(f"[Report] Saved to GCS: gs://{bucket_name}/{blob_path}")
        return f"gs://{bucket_name}/{blob_path}"

    except Exception as e:
        print(f"[Report] GCS save error: {e}")
        return None


# Flask endpoint handler (called from app.py)
def generate_report_handler(report_date=None):
    """
    Handler for /generate-report endpoint
    Returns report data and optionally sends email
    """
    if report_date is None:
        # Default to yesterday
        report_date = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        report = generate_daily_report(report_date)

        email_sent = False
        if SENDGRID_API_KEY and REPORT_EMAIL_TO:
            email_sent = send_report_email(report, report_date)

        return {
            'success': True,
            'date': report_date,
            'participants': report['total_participants'],
            'email_sent': email_sent,
            'email_to': REPORT_EMAIL_TO if email_sent else None
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': str(e)
        }


if __name__ == '__main__':
    # Test report generation
    import sys

    if len(sys.argv) > 1:
        date = sys.argv[1]
    else:
        date = datetime.utcnow().strftime('%Y-%m-%d')

    print(f"Generating report for {date}...")
    report = generate_daily_report(date)

    # Save CSV locally for testing
    filename = f"attendance_report_{date}.csv"
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        f.write(report['csv_content'])
    print(f"Saved: {filename}")

    print(f"\nReport generated with {report['total_participants']} participants")

    # Show first few rows
    print("\nFirst 5 participants:")
    for p in report['participants'][:5]:
        print(f"  {p.get('Name', '')} - {p.get('Main_Joined_IST', '')} to {p.get('Main_Left_IST', '')}")
