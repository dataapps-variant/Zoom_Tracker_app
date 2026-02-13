/**
 * API Service
 * Handles communication with your backend server
 */

import axios from 'axios';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || 'http://localhost:8080';

const api = axios.create({
  baseURL: BACKEND_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
});

/**
 * Notify backend that calibration is starting
 */
export async function notifyCalibrationStart(meetingId, meetingUUID) {
  try {
    const response = await api.post('/calibration/start', {
      meeting_id: meetingId,
      meeting_uuid: meetingUUID,
      started_at: new Date().toISOString()
    });
    return response.data;
  } catch (err) {
    console.error('Failed to notify calibration start:', err);
    // Don't throw - calibration can continue without backend notification
    return null;
  }
}

/**
 * Send room mapping data to backend
 */
export async function sendRoomMapping(meetingId, meetingUUID, roomMapping) {
  try {
    const response = await api.post('/calibration/mapping', {
      meeting_id: meetingId,
      meeting_uuid: meetingUUID,
      room_mapping: roomMapping.map(room => ({
        room_uuid: room.roomUUID,
        room_name: room.roomName,
        room_index: room.roomIndex,
        mapped_at: room.timestamp
      })),
      completed_at: new Date().toISOString()
    });
    return response.data;
  } catch (err) {
    console.error('Failed to send room mapping:', err);
    throw err;
  }
}

/**
 * Notify backend that calibration is complete
 */
export async function notifyCalibrationComplete(meetingId, meetingUUID, result) {
  try {
    const response = await api.post('/calibration/complete', {
      meeting_id: meetingId,
      meeting_uuid: meetingUUID,
      success: result.success,
      total_rooms: result.totalRooms,
      mapped_rooms: result.mappedRooms,
      completed_at: new Date().toISOString()
    });
    return response.data;
  } catch (err) {
    console.error('Failed to notify calibration complete:', err);
    return null;
  }
}

/**
 * Get existing room mappings for a meeting
 */
export async function getExistingMappings(meetingId) {
  try {
    const response = await api.get(`/calibration/mappings/${meetingId}`);
    return response.data.mappings || [];
  } catch (err) {
    console.error('Failed to get existing mappings:', err);
    return [];
  }
}

/**
 * Health check for backend connection
 */
export async function checkBackendHealth() {
  try {
    const response = await api.get('/health');
    return response.data.status === 'ok';
  } catch (err) {
    return false;
  }
}

export default {
  notifyCalibrationStart,
  sendRoomMapping,
  notifyCalibrationComplete,
  getExistingMappings,
  checkBackendHealth
};
