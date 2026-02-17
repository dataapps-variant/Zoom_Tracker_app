"""
DAILY REPORT GENERATOR
======================
Generates detailed attendance reports with:
- Participant attendance times
- Camera ON/OFF exact times and durations
- Room visit history
- QoS data
- CSV export support

Triggered by Cloud Scheduler daily at 9:15 AM
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
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID', '')
BQ_DATASET = os.environ.get('BQ_DATASET', 'breakout_room_calibrator')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
REPORT_EMAIL_FROM = os.environ.get('REPORT_EMAIL_FROM', 'reports@yourdomain.com')
REPORT_EMAIL_TO = os.environ.get('REPORT_EMAIL_TO', '')


def get_bq_client():
    """Get BigQuery client"""
    return bigquery.Client(project=GCP_PROJECT_ID)


def generate_daily_report(report_date=None):
    """
    Generate complete daily attendance report

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
    # 1. PARTICIPANT SUMMARY
    # =============================================
    summary_query = f"""
    WITH participant_events AS (
        SELECT
            participant_id,
            participant_name,
            participant_email,
            event_type,
            event_timestamp,
            room_name,
            LEAD(event_timestamp) OVER (
                PARTITION BY participant_id
                ORDER BY event_timestamp
            ) AS next_event_time
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.participant_events`
        WHERE event_date = '{report_date}'
    ),
    attendance AS (
        SELECT
            participant_id,
            participant_name,
            participant_email,
            MIN(event_timestamp) AS first_join,
            MAX(event_timestamp) AS last_activity,
            COUNT(DISTINCT CASE WHEN event_type LIKE '%joined%' THEN event_timestamp END) AS join_count,
            STRING_AGG(DISTINCT room_name, ', ' ORDER BY room_name) AS rooms_visited
        FROM participant_events
        GROUP BY 1, 2, 3
    )
    SELECT
        participant_id,
        participant_name,
        participant_email,
        first_join,
        last_activity,
        TIMESTAMP_DIFF(
            SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', SUBSTR(last_activity, 1, 19)),
            SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', SUBSTR(first_join, 1, 19)),
            MINUTE
        ) AS total_duration_minutes,
        join_count,
        rooms_visited
    FROM attendance
    ORDER BY participant_name
    """

    try:
        summary_results = list(client.query(summary_query).result())
    except Exception as e:
        print(f"[Report] Summary query error: {e}")
        summary_results = []

    # =============================================
    # 2. CAMERA ON/OFF DETAILS
    # =============================================
    camera_query = f"""
    SELECT
        participant_id,
        participant_name,
        participant_email,
        event_type,
        event_timestamp,
        event_time,
        room_name,
        duration_seconds,
        camera_on
    FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.camera_events`
    WHERE event_date = '{report_date}'
    ORDER BY participant_name, event_timestamp
    """

    try:
        camera_results = list(client.query(camera_query).result())
    except Exception as e:
        print(f"[Report] Camera query error: {e}")
        camera_results = []

    # =============================================
    # 3. CAMERA DURATION SUMMARY
    # =============================================
    camera_summary_query = f"""
    SELECT
        participant_name,
        participant_email,
        SUM(CASE WHEN duration_seconds IS NOT NULL THEN duration_seconds ELSE 0 END) AS total_camera_on_seconds,
        COUNT(CASE WHEN camera_on = TRUE THEN 1 END) AS camera_on_count,
        COUNT(CASE WHEN camera_on = FALSE THEN 1 END) AS camera_off_count,
        MIN(CASE WHEN camera_on = TRUE THEN event_time END) AS first_camera_on,
        MAX(CASE WHEN camera_on = FALSE THEN event_time END) AS last_camera_off
    FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.camera_events`
    WHERE event_date = '{report_date}'
    GROUP BY participant_name, participant_email
    ORDER BY participant_name
    """

    try:
        camera_summary = list(client.query(camera_summary_query).result())
    except Exception as e:
        print(f"[Report] Camera summary error: {e}")
        camera_summary = []

    # =============================================
    # 4. ROOM VISIT HISTORY
    # =============================================
    room_history_query = f"""
    WITH room_visits AS (
        SELECT
            participant_id,
            participant_name,
            participant_email,
            room_name,
            event_type,
            event_timestamp,
            LEAD(event_timestamp) OVER (
                PARTITION BY participant_id
                ORDER BY event_timestamp
            ) AS next_event_time
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.participant_events`
        WHERE event_date = '{report_date}'
            AND room_name IS NOT NULL
            AND room_name != ''
    )
    SELECT
        participant_id,
        participant_name,
        participant_email,
        room_name,
        MIN(event_timestamp) AS entered_at,
        MAX(COALESCE(next_event_time, event_timestamp)) AS left_at,
        GREATEST(1, TIMESTAMP_DIFF(
            SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', SUBSTR(MAX(COALESCE(next_event_time, event_timestamp)), 1, 19)),
            SAFE.PARSE_TIMESTAMP('%Y-%m-%dT%H:%M:%S', SUBSTR(MIN(event_timestamp), 1, 19)),
            MINUTE
        )) AS duration_minutes
    FROM room_visits
    WHERE event_type LIKE '%joined%'
    GROUP BY participant_id, participant_name, participant_email, room_name
    ORDER BY participant_name, entered_at
    """

    try:
        room_history = list(client.query(room_history_query).result())
    except Exception as e:
        print(f"[Report] Room history error: {e}")
        room_history = []

    # =============================================
    # 5. QOS DATA
    # =============================================
    qos_query = f"""
    SELECT
        participant_name,
        participant_email,
        join_time,
        leave_time,
        duration_minutes,
        attentiveness_score
    FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.qos_data`
    WHERE event_date = '{report_date}'
    ORDER BY participant_name
    """

    try:
        qos_results = list(client.query(qos_query).result())
    except Exception as e:
        print(f"[Report] QoS query error: {e}")
        qos_results = []

    # =============================================
    # BUILD REPORT OBJECT
    # =============================================

    # Convert BigQuery rows to dicts
    def row_to_dict(row):
        return dict(row.items())

    report = {
        'report_date': report_date,
        'generated_at': datetime.utcnow().isoformat(),
        'summary': {
            'total_participants': len(summary_results),
            'participants': [row_to_dict(r) for r in summary_results]
        },
        'camera_details': [row_to_dict(r) for r in camera_results],
        'camera_summary': [row_to_dict(r) for r in camera_summary],
        'room_history': [row_to_dict(r) for r in room_history],
        'qos_data': [row_to_dict(r) for r in qos_results]
    }

    # Generate CSV files
    report['csv_files'] = generate_csv_files(report)

    print(f"[Report] Generated report with {len(summary_results)} participants")
    return report


def generate_csv_files(report):
    """Generate single consolidated timeline report CSV"""
    csv_files = {}

    # SINGLE CONSOLIDATED TIMELINE REPORT
    # Shows complete journey for each participant with camera status per room
    timeline_output = io.StringIO()
    timeline_writer = csv.writer(timeline_output)

    # Header
    timeline_writer.writerow([
        'Participant Name',
        'Email',
        'Participant ID',
        'Meeting Join Time',
        'Meeting Leave Time',
        'Total Duration (min)',
        'Room Name',
        'Room Entry Time',
        'Room Exit Time',
        'Room Duration (min)',
        'Camera ON Time',
        'Camera OFF Time',
        'Camera ON Duration (sec)',
        'Next Room'
    ])

    # Build participant timeline from all events
    participant_data = {}

    # Step 1: Gather all participant info from summary
    for p in report['summary']['participants']:
        pid = p.get('participant_name', '')
        participant_data[pid] = {
            'name': p.get('participant_name', ''),
            'email': p.get('participant_email', ''),
            'participant_id': p.get('participant_id', ''),
            'meeting_join': p.get('first_join', ''),
            'meeting_leave': p.get('last_activity', ''),
            'total_duration': p.get('total_duration_minutes', 0) or 0,
            'room_visits': [],  # List of room visit records
            'camera_events': []  # List of camera events
        }

    # Step 2: Add room visit data
    for r in report['room_history']:
        name = r.get('participant_name', '')
        if name in participant_data:
            if not participant_data[name]['participant_id'] and r.get('participant_id'):
                participant_data[name]['participant_id'] = r.get('participant_id', '')
            participant_data[name]['room_visits'].append({
                'room_name': r.get('room_name', ''),
                'entry_time': r.get('entered_at', ''),
                'exit_time': r.get('left_at', ''),
                'duration': r.get('duration_minutes', 0)
            })

    # Step 3: Add camera events
    for c in report['camera_details']:
        name = c.get('participant_name', '')
        if name in participant_data:
            if not participant_data[name]['participant_id'] and c.get('participant_id'):
                participant_data[name]['participant_id'] = c.get('participant_id', '')
            participant_data[name]['camera_events'].append({
                'event_type': c.get('event_type', ''),
                'event_time': c.get('event_time', ''),
                'room_name': c.get('room_name', ''),
                'camera_on': c.get('camera_on', False),
                'duration_seconds': c.get('duration_seconds', 0)
            })

    # Step 4: Build timeline rows
    for name, data in sorted(participant_data.items()):
        room_visits = data['room_visits']
        camera_events = data['camera_events']

        if not room_visits:
            # No room visits - just main meeting
            # Find camera events for main room
            main_camera_on = ''
            main_camera_off = ''
            main_camera_duration = 0

            for ce in camera_events:
                if ce['camera_on']:
                    if not main_camera_on:
                        main_camera_on = ce['event_time']
                else:
                    main_camera_off = ce['event_time']
                    main_camera_duration += ce.get('duration_seconds', 0) or 0

            timeline_writer.writerow([
                data['name'],
                data['email'],
                data['participant_id'],
                data['meeting_join'][:19] if data['meeting_join'] else '',
                data['meeting_leave'][:19] if data['meeting_leave'] else '',
                data['total_duration'],
                'Main Room',
                data['meeting_join'][:19] if data['meeting_join'] else '',
                data['meeting_leave'][:19] if data['meeting_leave'] else '',
                data['total_duration'],
                main_camera_on,
                main_camera_off,
                main_camera_duration,
                ''
            ])
        else:
            # Has room visits - create row per room
            for i, room in enumerate(room_visits):
                # Get next room name
                next_room = room_visits[i + 1]['room_name'] if i + 1 < len(room_visits) else 'Left Meeting'

                # Find camera events for this room
                room_camera_on = ''
                room_camera_off = ''
                room_camera_duration = 0

                for ce in camera_events:
                    if ce.get('room_name', '') == room['room_name']:
                        if ce['camera_on']:
                            if not room_camera_on:
                                room_camera_on = ce['event_time']
                        else:
                            room_camera_off = ce['event_time']
                            room_camera_duration += ce.get('duration_seconds', 0) or 0

                timeline_writer.writerow([
                    data['name'],
                    data['email'],
                    data['participant_id'],
                    data['meeting_join'][:19] if data['meeting_join'] else '',
                    data['meeting_leave'][:19] if data['meeting_leave'] else '',
                    data['total_duration'],
                    room['room_name'],
                    room['entry_time'][:19] if room['entry_time'] else '',
                    room['exit_time'][:19] if room['exit_time'] else '',
                    room['duration'],
                    room_camera_on,
                    room_camera_off,
                    room_camera_duration,
                    next_room
                ])

    csv_files['attendance_report.csv'] = timeline_output.getvalue()

    return csv_files


def send_report_email(report, report_date):
    """Send report via SendGrid with CSV attachments"""
    if not SENDGRID_AVAILABLE:
        print("[Report] SendGrid not available")
        return False

    if not all([SENDGRID_API_KEY, REPORT_EMAIL_FROM, REPORT_EMAIL_TO]):
        print("[Report] Email configuration incomplete")
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
                th, td {{ border: 1px solid #ddd; padding: 10px; text-align: left; }}
                th {{ background-color: #2D8CFF; color: white; }}
                tr:nth-child(even) {{ background-color: #f9f9f9; }}
                .summary {{ background: #f0f8ff; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .footer {{ color: #666; font-size: 12px; margin-top: 30px; padding-top: 20px; border-top: 1px solid #ddd; }}
            </style>
        </head>
        <body>
            <h1>Daily Zoom Attendance Report</h1>
            <p><strong>Date:</strong> {report_date}</p>
            <p><strong>Generated:</strong> {report['generated_at']}</p>

            <div class="summary">
                <h2>Summary</h2>
                <p><strong>Total Participants:</strong> {report['summary']['total_participants']}</p>
            </div>

            <h2>Attendance Summary</h2>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Email</th>
                    <th>First Join</th>
                    <th>Duration (min)</th>
                    <th>Rooms</th>
                </tr>
        """

        for p in report['summary']['participants'][:50]:  # Limit to 50 in email
            html_content += f"""
                <tr>
                    <td>{p.get('participant_name', '')}</td>
                    <td>{p.get('participant_email', '')}</td>
                    <td>{p.get('first_join', '')[:19] if p.get('first_join') else ''}</td>
                    <td>{p.get('total_duration_minutes', '')}</td>
                    <td>{p.get('rooms_visited', '')[:50]}...</td>
                </tr>
            """

        html_content += """
            </table>

            <h2>Camera Usage Summary</h2>
            <table>
                <tr>
                    <th>Name</th>
                    <th>Camera ON (min)</th>
                    <th>ON Count</th>
                    <th>OFF Count</th>
                </tr>
        """

        for c in report['camera_summary'][:50]:
            total_seconds = c.get('total_camera_on_seconds', 0) or 0
            html_content += f"""
                <tr>
                    <td>{c.get('participant_name', '')}</td>
                    <td>{round(total_seconds / 60, 2)}</td>
                    <td>{c.get('camera_on_count', '')}</td>
                    <td>{c.get('camera_off_count', '')}</td>
                </tr>
            """

        html_content += """
            </table>

            <div class="footer">
                <p>Complete attendance data available in the attached CSV file:</p>
                <ul>
                    <li><strong>attendance_report.csv</strong> - Complete timeline with all details</li>
                </ul>
                <p>CSV includes: Name, Email, ID, Join/Leave times, Room visits, Camera ON/OFF times per room, Next room visited</p>
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

        # Attach CSV files
        for filename, content in report['csv_files'].items():
            encoded = base64.b64encode(content.encode()).decode()
            attachment = Attachment(
                FileContent(encoded),
                FileName(f"{report_date}_{filename}"),
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
        return False


def save_csv_to_gcs(report, report_date, bucket_name):
    """Save CSV files to Google Cloud Storage"""
    from google.cloud import storage

    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)

        for filename, content in report['csv_files'].items():
            blob_path = f"reports/{report_date}/{filename}"
            blob = bucket.blob(blob_path)
            blob.upload_from_string(content, content_type='text/csv')
            print(f"[Report] Saved to GCS: gs://{bucket_name}/{blob_path}")

        return True
    except Exception as e:
        print(f"[Report] GCS save error: {e}")
        return False


# Cloud Function entry point
def generate_report_http(request):
    """
    HTTP Cloud Function entry point
    Triggered by Cloud Scheduler
    """
    # Get date from request or use yesterday
    request_json = request.get_json(silent=True) or {}
    report_date = request_json.get('date')

    if not report_date:
        report_date = (datetime.utcnow() - timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        report = generate_daily_report(report_date)

        # Send email
        if SENDGRID_API_KEY and REPORT_EMAIL_TO:
            send_report_email(report, report_date)

        return {
            'success': True,
            'date': report_date,
            'participants': report['summary']['total_participants']
        }

    except Exception as e:
        return {'error': str(e)}, 500


if __name__ == '__main__':
    # Test report generation
    import sys

    if len(sys.argv) > 1:
        date = sys.argv[1]
    else:
        date = datetime.utcnow().strftime('%Y-%m-%d')

    print(f"Generating report for {date}...")
    report = generate_daily_report(date)

    # Save CSVs locally for testing
    for filename, content in report['csv_files'].items():
        with open(f"test_{filename}", 'w', newline='', encoding='utf-8') as f:
            f.write(content)
        print(f"Saved: test_{filename}")

    print(f"\nReport generated with {report['summary']['total_participants']} participants")
