
import csv
import json
from datetime import datetime
from collections import defaultdict

# ==============================================================================
# DETAILED PER-ROOM REPORT GENERATOR
# ==============================================================================
# Format: Each row = One participant's session in ONE specific breakout room

RAW_PAYLOADS = "zoom_raw_payloads.json"
VIDEO_LOG = "meeting_video_status_report.csv"
OUTPUT_FILE = "DETAILED_ROOM_REPORT.csv"

def parse_video_data():
    """Parse video status to get per-minute video status"""
    video_timeline = {}
    
    try:
        with open(VIDEO_LOG, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                time_str = row.get('Time', '')
                if not time_str:
                    continue
                    
                for user, status in row.items():
                    if user != 'Time' and status:
                        # Extract just the name part
                        name = user.split('(')[0].strip()
                        if name not in video_timeline:
                            video_timeline[name] = {}
                        video_timeline[name][time_str] = status
    except Exception as e:
        print(f"Video data error: {e}")
    
    return video_timeline

def parse_room_sessions():
    """Parse raw payloads to build room sessions"""
    
    # Structure: {user: [{room, join_time, leave_time}, ...]}
    user_sessions = defaultdict(list)
    room_names = {}  # Map room_uuid to a friendly name
    room_counter = 1
    
    try:
        with open(RAW_PAYLOADS, 'r') as f:
            content = f.read()
            content = "[" + content.rstrip(",\n") + "]"
            events = json.loads(content)
        
        # Sort events by timestamp
        events.sort(key=lambda x: x.get('event_ts', 0))
        
        # Track open sessions
        open_sessions = {}  # {(user, room_uuid): session_data}
        
        for event in events:
            event_type = event.get('event', '')
            payload = event.get('payload', {}).get('object', {})
            participant = payload.get('participant', {})
            
            if not participant:
                continue
            
            user_name = participant.get('user_name', 'Unknown')
            email = participant.get('email', '')
            room_uuid = payload.get('breakout_room_uuid', 'MAIN')
            
            # Create friendly room name
            if room_uuid not in room_names:
                room_names[room_uuid] = f"Room {room_counter}"
                room_counter += 1
            
            room_name = room_names[room_uuid]
            
            if 'joined' in event_type:
                join_time = participant.get('join_time', '')
                
                # Start a new session
                session_key = (user_name, room_uuid)
                open_sessions[session_key] = {
                    'user_name': user_name,
                    'email': email,
                    'room_name': room_name,
                    'room_uuid': room_uuid,
                    'join_time': join_time,
                    'leave_time': None
                }
                
            elif 'left' in event_type:
                leave_time = participant.get('leave_time', '')
                
                # Find matching open session
                session_key = (user_name, room_uuid)
                
                if session_key in open_sessions:
                    session = open_sessions.pop(session_key)
                    session['leave_time'] = leave_time
                    user_sessions[user_name].append(session)
                else:
                    # No matching join, create a partial session
                    user_sessions[user_name].append({
                        'user_name': user_name,
                        'email': email,
                        'room_name': room_name,
                        'room_uuid': room_uuid,
                        'join_time': None,
                        'leave_time': leave_time
                    })
        
        # Close any remaining open sessions
        for session_key, session in open_sessions.items():
            session['leave_time'] = 'Still In Room'
            user_sessions[session['user_name']].append(session)
            
    except Exception as e:
        print(f"Error parsing payloads: {e}")
        import traceback
        traceback.print_exc()
    
    return user_sessions

def calculate_duration(join_time, leave_time):
    """Calculate duration between two ISO timestamps"""
    try:
        if not join_time or not leave_time or leave_time == 'Still In Room':
            return 'N/A'
        
        start = datetime.fromisoformat(join_time.replace('Z', '+00:00'))
        end = datetime.fromisoformat(leave_time.replace('Z', '+00:00'))
        
        diff = end - start
        total_seconds = int(diff.total_seconds())
        
        if total_seconds < 0:
            return 'N/A'
        
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        
        return f"{minutes}m {seconds}s"
        
    except:
        return 'N/A'

def format_time(iso_time):
    """Convert ISO time to readable format"""
    try:
        if not iso_time or iso_time == 'Still In Room':
            return iso_time or 'N/A'
        
        dt = datetime.fromisoformat(iso_time.replace('Z', '+00:00'))
        # Convert to IST (UTC+5:30)
        from datetime import timedelta
        dt_ist = dt + timedelta(hours=5, minutes=30)
        return dt_ist.strftime('%H:%M:%S')
    except:
        return iso_time

def generate_detailed_report():
    print("="*60)
    print("GENERATING DETAILED PER-ROOM REPORT")
    print("="*60)
    
    # Get room sessions
    user_sessions = parse_room_sessions()
    print(f"Found {len(user_sessions)} participants with breakout room activity")
    
    # Get video data
    video_data = parse_video_data()
    print(f"Found video data for {len(video_data)} participants")
    
    # Build report rows
    report_rows = []
    
    for user_name, sessions in user_sessions.items():
        for session in sessions:
            join_time = session['join_time']
            leave_time = session['leave_time']
            room_name = session['room_name']
            email = session['email']
            
            # Calculate duration in room
            duration = calculate_duration(join_time, leave_time)
            
            # Calculate video on/off time (estimate based on room duration)
            video_on = 'N/A'
            video_off = 'N/A'
            
            # Check video data for this user
            for vid_user, timeline in video_data.items():
                if user_name.split()[0].lower() in vid_user.lower():
                    on_count = sum(1 for status in timeline.values() if status == 'VIDEO ON')
                    off_count = sum(1 for status in timeline.values() if status != 'VIDEO ON')
                    video_on = f"{on_count} mins"
                    video_off = f"{off_count} mins"
                    break
            
            report_rows.append({
                'Participant Name': user_name,
                'Email': email,
                'Room Name': room_name,
                'Room Joined Time (IST)': format_time(join_time),
                'Room Left Time (IST)': format_time(leave_time),
                'Duration in Room': duration,
                'Camera ON Time': video_on,
                'Camera OFF Time': video_off
            })
    
    # Sort by participant name then by join time
    report_rows.sort(key=lambda x: (x['Participant Name'], x['Room Joined Time (IST)']))
    
    # Write CSV
    with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
        fieldnames = ['Participant Name', 'Email', 'Room Name', 
                      'Room Joined Time (IST)', 'Room Left Time (IST)', 
                      'Duration in Room', 'Camera ON Time', 'Camera OFF Time']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)
    
    print(f"\nReport saved to: {OUTPUT_FILE}")
    print(f"Total rows: {len(report_rows)}")
    
    # Print preview
    print("\n" + "="*100)
    print("PREVIEW (First 15 rows):")
    print("="*100)
    print(f"{'Name':<25} {'Room':<10} {'Joined':<12} {'Left':<12} {'Duration':<12} {'Cam ON':<10}")
    print("-"*100)
    
    for row in report_rows[:15]:
        print(f"{row['Participant Name'][:24]:<25} {row['Room Name']:<10} {row['Room Joined Time (IST)']:<12} {row['Room Left Time (IST)']:<12} {row['Duration in Room']:<12} {row['Camera ON Time']:<10}")

if __name__ == "__main__":
    generate_detailed_report()
