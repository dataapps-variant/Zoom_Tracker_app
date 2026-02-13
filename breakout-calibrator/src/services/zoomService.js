/**
 * Zoom Calibration Service
 * Handles the core logic for moving scout bot through breakout rooms
 *
 * ENHANCED with retry logic and room caching
 */

const BOT_NAME = process.env.REACT_APP_BOT_NAME || 'Scout Bot';
const BOT_EMAIL = process.env.REACT_APP_BOT_EMAIL || '';
const MOVE_DELAY_MS = 3000; // 3 seconds between room moves
const MAX_RETRIES = 2;

// Room mapping cache (persists across calibration runs)
const roomCache = new Map();

/**
 * Find the scout bot in the participant list
 * Tries multiple matching strategies
 */
export function findScoutBot(participants, botName = BOT_NAME, botEmail = BOT_EMAIL) {
  const normalizedBotName = botName.toLowerCase();
  const normalizedBotEmail = botEmail.toLowerCase();

  // Helper to get participant name from various possible fields
  const getName = (p) => {
    return p.screenName || p.displayName || p.participantName || p.name || p.userName || p.user_name || '';
  };

  // Helper to get participant email
  const getEmail = (p) => {
    return p.email || p.participantEmail || p.user_email || '';
  };

  // Debug: Log all participants with all their fields
  console.log('Looking for bot:', botName);
  console.log('Raw participants:', JSON.stringify(participants, null, 2));
  console.log('Participant names:', participants.map(p => getName(p)));

  // Strategy 1: Exact email match (most reliable)
  if (normalizedBotEmail) {
    const byEmail = participants.find(p => {
      const email = getEmail(p).toLowerCase();
      return email === normalizedBotEmail;
    });
    if (byEmail) {
      console.log('Found scout bot by email match');
      return byEmail;
    }
  }

  // Strategy 2: Exact name match
  const byExactName = participants.find(p => {
    const name = getName(p).toLowerCase();
    return name === normalizedBotName;
  });
  if (byExactName) {
    console.log('Found scout bot by exact name match');
    return byExactName;
  }

  // Strategy 3: Partial name match
  const byPartialName = participants.find(p => {
    const name = getName(p).toLowerCase();
    return name.includes(normalizedBotName) ||
           name.includes('scout') ||
           name.includes('bot');
  });
  if (byPartialName) {
    console.log('Found scout bot by partial name match');
    return byPartialName;
  }

  console.log('Scout bot not found in', participants.length, 'participants');
  return null;
}

/**
 * Sleep utility for delays between room moves
 */
export function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Get cached room name (if previously mapped)
 */
export function getCachedRoomName(roomUUID) {
  return roomCache.get(roomUUID);
}

/**
 * Cache a room mapping
 */
export function cacheRoomMapping(roomUUID, roomName) {
  roomCache.set(roomUUID, roomName);
}

/**
 * Get all cached mappings
 */
export function getAllCachedMappings() {
  return Array.from(roomCache.entries()).map(([uuid, name]) => ({
    roomUUID: uuid,
    roomName: name
  }));
}

/**
 * Clear the room cache
 */
export function clearRoomCache() {
  roomCache.clear();
}

/**
 * Move participant with retry logic
 */
async function moveWithRetry(moveParticipantToRoom, botUUID, roomUUID, roomName, maxRetries = MAX_RETRIES) {
  let lastError;

  for (let attempt = 1; attempt <= maxRetries + 1; attempt++) {
    try {
      const response = await moveParticipantToRoom(botUUID, roomUUID);
      console.log(`>>> MOVE RESPONSE for ${roomName}:`, JSON.stringify(response));
      return { success: true, attempts: attempt, response, sdkResponse: response };
    } catch (err) {
      lastError = err;
      console.warn(`Move attempt ${attempt} failed for ${roomName}:`, err.message);

      if (attempt <= maxRetries) {
        // Wait before retry (exponential backoff)
        await sleep(1000 * attempt);
      }
    }
  }

  return { success: false, error: lastError, attempts: maxRetries + 1 };
}

/**
 * Run the calibration sequence
 * Moves scout bot through all breakout rooms and builds the mapping
 *
 * @param {Object} options
 * @param {Function} options.getBreakoutRooms - SDK method to get rooms
 * @param {Function} options.getParticipants - SDK method to get participants
 * @param {Function} options.moveParticipantToRoom - SDK method to move participant
 * @param {Function} options.moveToMainRoom - SDK method to return to main room
 * @param {Function} options.onProgress - Callback for progress updates
 * @param {Function} options.onRoomMapped - Callback when a room is mapped
 * @param {number} options.delayMs - Delay between room moves (default 3000)
 * @param {boolean} options.useCache - Use cached mappings if available (default true)
 */
export async function runCalibration({
  getBreakoutRooms,
  getParticipants,
  moveParticipantToRoom,
  moveToMainRoom,
  onProgress,
  onRoomMapped,
  delayMs = MOVE_DELAY_MS,
  useCache = true
}) {
  const roomMapping = [];
  const errors = [];

  // Step 1: Get all breakout rooms
  onProgress?.({ step: 'fetching_rooms', message: 'Fetching breakout rooms...' });
  const rooms = await getBreakoutRooms();

  if (!rooms || rooms.length === 0) {
    throw new Error('No breakout rooms found. Make sure breakout rooms are created and open.');
  }

  onProgress?.({
    step: 'rooms_found',
    message: `Found ${rooms.length} breakout rooms`,
    totalRooms: rooms.length
  });

  // Check cache for already-mapped rooms
  let cachedCount = 0;
  if (useCache) {
    for (const room of rooms) {
      // Use breakoutRoomId WITH curly braces (don't strip them!)
    const roomUUID = room.breakoutRoomId || room.breakoutRoomUUID || room.breakoutroomid || room.uuid || room.id;
      const cachedName = getCachedRoomName(roomUUID);
      if (cachedName) {
        cachedCount++;
      }
    }
    if (cachedCount > 0) {
      onProgress?.({
        step: 'cache_hit',
        message: `${cachedCount} rooms already cached from previous calibration`
      });
    }
  }

  // Step 2: Find the scout bot
  onProgress?.({ step: 'finding_bot', message: 'Looking for scout bot...' });
  const participants = await getParticipants();

  // Show participants found for debugging - check all possible name fields
  const getParticipantName = (p) => p.screenName || p.displayName || p.participantName || p.name || p.userName || p.user_name || 'NoName';
  const participantNames = participants.map(p => getParticipantName(p)).join(', ');
  const participantKeys = participants.length > 0 ? Object.keys(participants[0]).join(', ') : 'none';

  onProgress?.({ step: 'participants_found', message: `Found ${participants.length}: ${participantNames} [Keys: ${participantKeys}]` });

  const scoutBot = findScoutBot(participants);

  if (!scoutBot) {
    throw new Error(`Bot "${BOT_NAME}" not found. Found: ${participantNames}. Keys: ${participantKeys}`);
  }

  // SDK expects participantUUID - prefer that field
  const botUUID = scoutBot.participantUUID || scoutBot.uuid || scoutBot.participantId || scoutBot.id;
  const botName = scoutBot.name || scoutBot.participantName || scoutBot.screenName || scoutBot.displayName || scoutBot.userName;

  console.log('Bot participant object:', JSON.stringify(scoutBot));
  console.log('Using botUUID:', botUUID);

  // DEBUG: Log full scout bot object to see all available fields
  console.log('=== SCOUT BOT FULL OBJECT ===');
  console.log(JSON.stringify(scoutBot, null, 2));
  console.log('Extracted botUUID:', botUUID);
  console.log('=============================');

  onProgress?.({
    step: 'bot_found',
    message: `Found scout bot: ${botName} (UUID: ${botUUID})`,
    botId: botUUID
  });

  // Step 3: Move bot through each room
  for (let i = 0; i < rooms.length; i++) {
    const room = rooms[i];
    const roomName = room.breakoutRoomName || room.name || `Room ${i + 1}`;
    // Use breakoutRoomId WITH curly braces (don't strip them!)
    const roomUUID = room.breakoutRoomId || room.breakoutRoomUUID || room.breakoutroomid || room.uuid || room.id;

    // Check cache first
    if (useCache && getCachedRoomName(roomUUID)) {
      const mapping = {
        roomUUID,
        roomName,
        roomIndex: i,
        timestamp: new Date().toISOString(),
        fromCache: true
      };
      roomMapping.push(mapping);
      onRoomMapped?.(mapping);

      onProgress?.({
        step: 'room_cached',
        message: `Cached: ${roomName}`,
        currentRoom: i + 1,
        totalRooms: rooms.length
      });
      continue;
    }

    // DEBUG: Log room object for first room
    if (i === 0) {
      console.log('=== FIRST ROOM FULL OBJECT ===');
      console.log(JSON.stringify(room, null, 2));
      console.log('Extracted roomUUID:', roomUUID);
      console.log('==============================');
    }

    onProgress?.({
      step: 'moving_to_room',
      message: `Moving to room ${i + 1}/${rooms.length}: ${roomName}`,
      currentRoom: i + 1,
      totalRooms: rooms.length,
      roomName,
      roomUUID: roomUUID,
      botUUID: botUUID
    });

    console.log(`>>> MOVE CALL: botUUID=${botUUID}, roomUUID=${roomUUID}`);

    // Move bot to this room with retry
    const moveResult = await moveWithRetry(moveParticipantToRoom, botUUID, roomUUID, roomName);

    if (moveResult.success) {
      // Wait for the move to complete and webhook to fire
      await sleep(delayMs);

      // Record the mapping
      const mapping = {
        roomUUID,
        roomName,
        roomIndex: i,
        timestamp: new Date().toISOString(),
        attempts: moveResult.attempts
      };

      roomMapping.push(mapping);
      cacheRoomMapping(roomUUID, roomName); // Add to cache
      onRoomMapped?.(mapping);

      onProgress?.({
        step: 'room_mapped',
        message: `Mapped: ${roomName}`,
        currentRoom: i + 1,
        totalRooms: rooms.length,
        mapping
      });
    } else {
      console.error(`Failed to move to room ${roomName}:`, moveResult.error);
      errors.push({
        roomName,
        roomUUID,
        error: moveResult.error?.message || 'Unknown error'
      });

      onProgress?.({
        step: 'room_error',
        message: `Failed to move to ${roomName}: ${moveResult.error?.message}`,
        error: moveResult.error?.message
      });
      // Continue with next room
    }
  }

  // Step 4: Return bot to main room
  onProgress?.({ step: 'returning', message: 'Returning scout bot to main room...' });

  try {
    await moveToMainRoom(botUUID);
    await sleep(1000);
  } catch (err) {
    console.warn('Failed to return bot to main room:', err);
    // Not critical, continue
  }

  const successCount = roomMapping.filter(m => !m.fromCache).length;
  const cachedUsed = roomMapping.filter(m => m.fromCache).length;

  onProgress?.({
    step: 'complete',
    message: `Calibration complete! Mapped ${successCount} rooms${cachedUsed > 0 ? ` (${cachedUsed} from cache)` : ''}.`,
    totalMapped: roomMapping.length,
    errors: errors.length
  });

  return {
    success: errors.length === 0,
    roomMapping,
    totalRooms: rooms.length,
    mappedRooms: roomMapping.length,
    newlyMapped: successCount,
    fromCache: cachedUsed,
    errors
  };
}

export default {
  findScoutBot,
  runCalibration,
  sleep,
  getCachedRoomName,
  cacheRoomMapping,
  getAllCachedMappings,
  clearRoomCache
};
