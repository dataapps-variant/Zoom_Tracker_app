import React, { useState, useCallback } from 'react';
import useZoomSdk from '../hooks/useZoomSdk';
import { runCalibration } from '../services/zoomService';
import {
  notifyCalibrationStart,
  sendRoomMapping,
  notifyCalibrationComplete
} from '../services/apiService';
import StatusMessage from './StatusMessage';
import ProgressIndicator from './ProgressIndicator';
import RoomList from './RoomList';

const UI_STATES = {
  IDLE: 'idle',
  CHECKING: 'checking',
  CALIBRATING: 'calibrating',
  COMPLETE: 'complete',
  ERROR: 'error'
};

function CalibrationPanel() {
  const {
    isConfigured,
    error: sdkError,
    meetingContext,
    isHost,
    getBreakoutRooms,
    getParticipants,
    moveParticipantToRoom,
    moveToMainRoom,
    getMeetingUUID,
    changeMyBreakoutRoom
  } = useZoomSdk();

  const [uiState, setUiState] = useState(UI_STATES.IDLE);
  const [statusMessage, setStatusMessage] = useState('');
  const [rooms, setRooms] = useState([]);
  const [mappedRooms, setMappedRooms] = useState([]);
  const [currentRoom, setCurrentRoom] = useState(-1);
  const [totalRooms, setTotalRooms] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');
  const [debugLogs, setDebugLogs] = useState([]);

  const handleStartCalibration = useCallback(async () => {
    if (!isConfigured) {
      setErrorMessage('Zoom SDK not configured');
      setUiState(UI_STATES.ERROR);
      return;
    }

    if (!isHost) {
      setErrorMessage('Only hosts or co-hosts can run calibration');
      setUiState(UI_STATES.ERROR);
      return;
    }

    try {
      setUiState(UI_STATES.CHECKING);
      setStatusMessage('Initializing calibration...');
      setMappedRooms([]);
      setCurrentRoom(-1);
      setErrorMessage('');
      setDebugLogs([]);

      // Get meeting info
      const meetingUUID = await getMeetingUUID();
      const meetingId = meetingContext?.meetingID;

      // Store meeting info for use in callbacks
      const meetingInfo = { meetingId, meetingUUID };

      // Notify backend
      await notifyCalibrationStart(meetingId, meetingUUID);
      setDebugLogs(prev => [...prev, `Notified backend: calibration started`]);

      // Fetch rooms first to show in UI
      const breakoutRooms = await getBreakoutRooms();
      setRooms(breakoutRooms);
      setTotalRooms(breakoutRooms.length);

      // DEBUG: Log first room object with all keys and values
      if (breakoutRooms.length > 0) {
        const room = breakoutRooms[0];
        const keys = Object.keys(room).join(', ');
        setDebugLogs(prev => [...prev, `ROOM KEYS: ${keys}`]);
        // Log each key-value pair
        for (const [key, value] of Object.entries(room)) {
          setDebugLogs(prev => [...prev, `  ${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`]);
        }
      }

      // DEBUG: Get and log participants
      const participants = await getParticipants();
      setDebugLogs(prev => [...prev, `PARTICIPANTS (${participants.length}): ${JSON.stringify(participants.map(p => ({name: p.screenName || p.participantName || p.name, uuid: p.participantUUID || p.participantId})))}`]);

      setUiState(UI_STATES.CALIBRATING);

      // Run calibration with BEFORE-MOVE mapping notification
      const result = await runCalibration({
        getBreakoutRooms,
        getParticipants,
        moveParticipantToRoom,
        moveToMainRoom,
        onProgress: async (progress) => {
          setStatusMessage(progress.message);
          if (progress.currentRoom !== undefined) {
            setCurrentRoom(progress.currentRoom - 1);
          }
          // Log bot found info
          if (progress.step === 'bot_found') {
            setDebugLogs(prev => [...prev, `BOT FOUND: ${progress.message}`]);
            setDebugLogs(prev => [...prev, `BOT UUID: ${progress.botId}`]);
          }
          // CRITICAL FIX: Send mapping to backend BEFORE moving Scout Bot
          // This tells the backend which room Scout Bot is about to enter
          if (progress.step === 'moving_to_room') {
            const mapping = {
              roomUUID: progress.roomUUID,
              roomName: progress.roomName,
              roomIndex: progress.currentRoom - 1,
              timestamp: new Date().toISOString()
            };
            try {
              await sendRoomMapping(meetingInfo.meetingId, meetingInfo.meetingUUID, [mapping]);
              setDebugLogs(prev => [...prev, `SENT MAPPING BEFORE MOVE: ${progress.roomName}`]);
            } catch (err) {
              setDebugLogs(prev => [...prev, `WARNING: Failed to send mapping: ${err.message}`]);
            }

            // Log first move attempt with full details
            if (progress.currentRoom === 1) {
              setDebugLogs(prev => [...prev, `FIRST MOVE: ${progress.message}`]);
              setDebugLogs(prev => [...prev, `CALLING SDK WITH:`]);
              setDebugLogs(prev => [...prev, `  participantUUID: ${progress.botUUID}`]);
              setDebugLogs(prev => [...prev, `  breakoutRoomUUID: ${progress.roomUUID}`]);
            }
          }
          // Log first room mapped (SDK response)
          if (progress.step === 'room_mapped' && progress.currentRoom === 1) {
            setDebugLogs(prev => [...prev, `SDK RESPONSE: ${JSON.stringify(progress.mapping)}`]);
          }
        },
        onRoomMapped: (mapping) => {
          setMappedRooms(prev => [...prev, mapping]);
        }
      });

      // Notify completion (mappings already sent before each move)
      await notifyCalibrationComplete(meetingId, meetingUUID, result);
      setDebugLogs(prev => [...prev, `Notified backend: calibration complete`]);

      setUiState(UI_STATES.COMPLETE);
      setStatusMessage(`Successfully mapped ${result.mappedRooms} of ${result.totalRooms} rooms`);
      setCurrentRoom(-1);

    } catch (err) {
      console.error('Calibration failed:', err);
      setErrorMessage(err.message || 'Calibration failed');
      setDebugLogs(prev => [...prev, `ERROR: ${err.message}`]);
      setDebugLogs(prev => [...prev, `ERROR CODE: ${err.code || 'none'}`]);
      setDebugLogs(prev => [...prev, `FULL ERROR: ${JSON.stringify(err)}`]);
      setUiState(UI_STATES.ERROR);
      setCurrentRoom(-1);
    }
  }, [
    isConfigured,
    isHost,
    meetingContext,
    getMeetingUUID,
    getBreakoutRooms,
    getParticipants,
    moveParticipantToRoom,
    moveToMainRoom
  ]);

  // Self-calibration: Move YOURSELF through rooms (when YOU are the scout bot)
  const handleSelfCalibration = useCallback(async () => {
    if (!isConfigured) {
      setErrorMessage('Zoom SDK not configured');
      setUiState(UI_STATES.ERROR);
      return;
    }

    try {
      setUiState(UI_STATES.CALIBRATING);
      setStatusMessage('Self-calibration: Moving through rooms...');
      setMappedRooms([]);
      setCurrentRoom(-1);
      setErrorMessage('');
      setDebugLogs(['=== SELF-CALIBRATION MODE ===', 'You will move through each room']);

      // Get meeting info
      const meetingUUID = await getMeetingUUID();
      const meetingId = meetingContext?.meetingID;

      // Notify backend that calibration is starting
      await notifyCalibrationStart(meetingId, meetingUUID);
      setDebugLogs(prev => [...prev, `Notified backend: calibration started`]);

      // Get rooms
      const breakoutRooms = await getBreakoutRooms();
      setRooms(breakoutRooms);
      setTotalRooms(breakoutRooms.length);

      setDebugLogs(prev => [...prev, `Found ${breakoutRooms.length} rooms`]);

      const mappings = [];

      // Move through each room
      for (let i = 0; i < breakoutRooms.length; i++) {
        const room = breakoutRooms[i];
        const roomName = room.name || `Room ${i + 1}`;
        const roomUUID = room.breakoutRoomId || room.uuid;
        const cleanUUID = roomUUID ? roomUUID.replace(/[{}]/g, '') : roomUUID;

        setStatusMessage(`Moving to room ${i + 1}/${breakoutRooms.length}: ${roomName}`);
        setCurrentRoom(i);

        if (i === 0) {
          setDebugLogs(prev => [...prev, `First room UUID: ${roomUUID}`, `Cleaned UUID: ${cleanUUID}`]);
        }

        try {
          // IMPORTANT: Send mapping to backend BEFORE moving
          // This tells the backend which room Scout Bot is about to enter
          const mapping = { room_uuid: cleanUUID, room_name: roomName, room_index: i };
          await sendRoomMapping(meetingId, meetingUUID, [mapping]);
          setDebugLogs(prev => [...prev, `Sent mapping to backend: ${roomName}`]);

          // Now actually move to the room
          const response = await changeMyBreakoutRoom(roomUUID);
          setDebugLogs(prev => [...prev, `Room ${i + 1} response: ${JSON.stringify(response)}`]);

          mappings.push({ roomUUID: cleanUUID, roomName, roomIndex: i });
          setMappedRooms([...mappings]);

          // Wait 5 seconds for webhook to arrive and be processed
          await new Promise(resolve => setTimeout(resolve, 5000));
        } catch (moveErr) {
          setDebugLogs(prev => [...prev, `ERROR moving to room ${i + 1}: ${moveErr.message}`]);
        }
      }

      // Notify backend that calibration is complete
      await notifyCalibrationComplete(meetingId, meetingUUID, {
        totalRooms: breakoutRooms.length,
        mappedRooms: mappings.length,
        success: true
      });
      setDebugLogs(prev => [...prev, `Notified backend: calibration complete`]);

      setUiState(UI_STATES.COMPLETE);
      setStatusMessage(`Self-calibration complete: ${mappings.length} rooms`);
      setCurrentRoom(-1);

    } catch (err) {
      console.error('Self-calibration failed:', err);
      setErrorMessage(err.message || 'Self-calibration failed');
      setDebugLogs(prev => [...prev, `ERROR: ${err.message}`]);
      setUiState(UI_STATES.ERROR);
      setCurrentRoom(-1);
    }
  }, [isConfigured, meetingContext, getMeetingUUID, getBreakoutRooms, changeMyBreakoutRoom]);

  const handleReset = useCallback(() => {
    setUiState(UI_STATES.IDLE);
    setStatusMessage('');
    setRooms([]);
    setMappedRooms([]);
    setCurrentRoom(-1);
    setTotalRooms(0);
    setErrorMessage('');
  }, []);

  // SDK not ready yet
  if (!isConfigured) {
    return (
      <div style={styles.container}>
        <div style={styles.header}>
          <h2 style={styles.title}>Breakout Room Calibrator</h2>
        </div>
        <StatusMessage
          status="checking"
          message={sdkError || "Connecting to Zoom..."}
        />
      </div>
    );
  }

  // Not a host - show limited options (can still move self)
  if (!isHost) {
    return (
      <div style={styles.container}>
        <div style={styles.header}>
          <h2 style={styles.title}>Breakout Room Calibrator</h2>
          <span style={styles.meetingId}>Non-host mode</span>
        </div>
        <StatusMessage
          status={uiState}
          message={errorMessage || statusMessage || "You can move yourself through rooms"}
        />

        {/* Progress */}
        {uiState === UI_STATES.CALIBRATING && totalRooms > 0 && (
          <ProgressIndicator
            current={mappedRooms.length}
            total={totalRooms}
            showSpinner={true}
          />
        )}

        <div style={styles.actions}>
          {uiState !== UI_STATES.CALIBRATING && (
            <button
              style={styles.primaryButton}
              onClick={handleSelfCalibration}
            >
              Move Myself Through Rooms
            </button>
          )}
          {uiState === UI_STATES.CALIBRATING && (
            <button style={styles.disabledButton} disabled>
              Moving...
            </button>
          )}
        </div>

        {/* Debug Logs */}
        {debugLogs.length > 0 && (
          <div style={styles.section}>
            <h3 style={{...styles.sectionTitle, color: '#ff6b6b'}}>DEBUG LOGS</h3>
            <pre style={{...styles.codeBlock, color: '#ff6b6b', maxHeight: '300px'}}>
              {debugLogs.join('\n\n')}
            </pre>
          </div>
        )}
      </div>
    );
  }

  return (
    <div style={styles.container}>
      {/* Header */}
      <div style={styles.header}>
        <h2 style={styles.title}>Breakout Room Calibrator</h2>
        {meetingContext && (
          <span style={styles.meetingId}>
            Meeting: {meetingContext.meetingID}
          </span>
        )}
      </div>

      {/* Status */}
      <StatusMessage
        status={uiState}
        message={errorMessage || statusMessage}
      />

      {/* Progress */}
      {uiState === UI_STATES.CALIBRATING && totalRooms > 0 && (
        <ProgressIndicator
          current={mappedRooms.length}
          total={totalRooms}
          showSpinner={true}
        />
      )}

      {/* Room List */}
      {rooms.length > 0 && (
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>Breakout Rooms</h3>
          <RoomList
            rooms={rooms}
            mappedRooms={mappedRooms}
            currentRoom={currentRoom}
          />
        </div>
      )}

      {/* Actions */}
      <div style={styles.actions}>
        {uiState === UI_STATES.IDLE && (
          <>
            <button
              style={styles.primaryButton}
              onClick={handleStartCalibration}
            >
              Move Scout Bot
            </button>
            <button
              style={{...styles.secondaryButton, marginLeft: '8px'}}
              onClick={handleSelfCalibration}
            >
              Move Myself
            </button>
          </>
        )}

        {uiState === UI_STATES.CALIBRATING && (
          <button style={styles.disabledButton} disabled>
            Calibrating...
          </button>
        )}

        {(uiState === UI_STATES.COMPLETE || uiState === UI_STATES.ERROR) && (
          <>
            <button
              style={styles.primaryButton}
              onClick={handleStartCalibration}
            >
              Run Again
            </button>
            <button
              style={styles.secondaryButton}
              onClick={handleReset}
            >
              Reset
            </button>
          </>
        )}
      </div>

      {/* Mapping Results */}
      {uiState === UI_STATES.COMPLETE && mappedRooms.length > 0 && (
        <div style={styles.section}>
          <h3 style={styles.sectionTitle}>Mapping Data</h3>
          <pre style={styles.codeBlock}>
            {JSON.stringify(mappedRooms, null, 2)}
          </pre>
        </div>
      )}

      {/* Debug Logs */}
      {debugLogs.length > 0 && (
        <div style={styles.section}>
          <h3 style={{...styles.sectionTitle, color: '#ff6b6b'}}>DEBUG LOGS</h3>
          <pre style={{...styles.codeBlock, color: '#ff6b6b', maxHeight: '300px'}}>
            {debugLogs.join('\n\n')}
          </pre>
        </div>
      )}
    </div>
  );
}

const styles = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    gap: '16px',
    padding: '20px',
    maxWidth: '500px',
    margin: '0 auto',
    minHeight: '100vh',
    backgroundColor: '#1a1a2e'
  },
  header: {
    display: 'flex',
    flexDirection: 'column',
    gap: '4px',
    marginBottom: '8px'
  },
  title: {
    color: '#fff',
    fontSize: '20px',
    fontWeight: '600',
    margin: 0
  },
  meetingId: {
    color: '#666',
    fontSize: '12px'
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px'
  },
  sectionTitle: {
    color: '#888',
    fontSize: '12px',
    fontWeight: '600',
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    margin: 0
  },
  actions: {
    display: 'flex',
    gap: '12px',
    marginTop: '8px'
  },
  primaryButton: {
    flex: 1,
    padding: '14px 24px',
    backgroundColor: '#2D8CFF',
    color: '#fff',
    border: 'none',
    borderRadius: '8px',
    fontSize: '14px',
    fontWeight: '600',
    cursor: 'pointer',
    transition: 'background-color 0.2s'
  },
  secondaryButton: {
    padding: '14px 24px',
    backgroundColor: 'transparent',
    color: '#888',
    border: '1px solid #333',
    borderRadius: '8px',
    fontSize: '14px',
    cursor: 'pointer'
  },
  disabledButton: {
    flex: 1,
    padding: '14px 24px',
    backgroundColor: '#333',
    color: '#666',
    border: 'none',
    borderRadius: '8px',
    fontSize: '14px',
    cursor: 'not-allowed'
  },
  codeBlock: {
    backgroundColor: 'rgba(0,0,0,0.3)',
    padding: '12px',
    borderRadius: '8px',
    fontSize: '11px',
    color: '#00C851',
    overflow: 'auto',
    maxHeight: '200px',
    fontFamily: 'Monaco, monospace'
  }
};

export default CalibrationPanel;
