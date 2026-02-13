/**
 * Express Backend Server
 * Handles OAuth callbacks, calibration data, and serves React app
 *
 * For Cloud Run deployment: serves both API and static React build
 */

const express = require('express');
const cors = require('cors');
const path = require('path');
const axios = require('axios');

const app = express();
const PORT = process.env.PORT || 3001;

// Middleware
app.use(cors());
app.use(express.json());

// OWASP Security Headers (required by Zoom Marketplace)
app.use((req, res, next) => {
  res.setHeader('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Content-Security-Policy', "default-src 'self' https://*.zoom.us https://*.zoomgov.com; script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.zoom.us; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self' https://*.zoom.us https://*.zoomgov.com wss://*.zoom.us; frame-ancestors https://*.zoom.us https://*.zoomgov.com");
  res.setHeader('X-Frame-Options', 'ALLOW-FROM https://zoom.us');
  res.setHeader('X-XSS-Protection', '1; mode=block');
  next();
});

// Serve static React build in production
if (process.env.NODE_ENV === 'production') {
  app.use(express.static(path.join(__dirname, 'public')));
}

// In-memory storage (replace with BigQuery in production)
const calibrationData = {
  sessions: {},      // meetingId -> session info
  mappings: {}       // meetingId -> room mappings
};

// =============================================================================
// OAuth Routes
// =============================================================================

/**
 * OAuth callback handler
 * Zoom redirects here after user authorizes the app
 */
app.get('/auth/callback', async (req, res) => {
  const { code } = req.query;

  if (!code) {
    return res.status(400).send('Missing authorization code');
  }

  try {
    // Exchange code for access token
    const tokenResponse = await axios.post(
      'https://zoom.us/oauth/token',
      null,
      {
        params: {
          grant_type: 'authorization_code',
          code,
          redirect_uri: process.env.REACT_APP_REDIRECT_URL || process.env.REDIRECT_URL
        },
        auth: {
          username: process.env.REACT_APP_ZOOM_CLIENT_ID || process.env.ZOOM_CLIENT_ID,
          password: process.env.REACT_APP_ZOOM_CLIENT_SECRET || process.env.ZOOM_CLIENT_SECRET
        }
      }
    );

    console.log('OAuth successful');

    // Redirect to app
    res.redirect('/?auth=success');

  } catch (err) {
    console.error('OAuth error:', err.response?.data || err.message);
    res.status(500).send('Authentication failed');
  }
});

// =============================================================================
// Calibration API Routes
// =============================================================================

/**
 * Health check
 */
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    service: 'Breakout Room Calibrator',
    timestamp: new Date().toISOString()
  });
});

/**
 * Start calibration session
 */
app.post('/calibration/start', (req, res) => {
  const { meeting_id, meeting_uuid, started_at } = req.body;

  if (!meeting_id) {
    return res.status(400).json({ error: 'Missing meeting_id' });
  }

  calibrationData.sessions[meeting_id] = {
    meeting_uuid,
    started_at,
    status: 'in_progress'
  };

  console.log(`[Calibration] Started for meeting ${meeting_id}`);

  res.json({
    success: true,
    message: 'Calibration session started'
  });
});

/**
 * Receive room mapping data
 */
app.post('/calibration/mapping', async (req, res) => {
  const { meeting_id, meeting_uuid, room_mapping, completed_at } = req.body;

  if (!meeting_id || !room_mapping) {
    return res.status(400).json({ error: 'Missing required fields' });
  }

  // Store mappings
  calibrationData.mappings[meeting_id] = {
    meeting_uuid,
    rooms: room_mapping,
    completed_at,
    count: room_mapping.length
  };

  console.log(`[Calibration] Received ${room_mapping.length} room mappings for meeting ${meeting_id}`);

  // Log each mapping
  room_mapping.forEach((room, i) => {
    console.log(`  ${i + 1}. ${room.room_name} -> ${room.room_uuid.substring(0, 8)}...`);
  });

  // Forward to main backend if configured
  const mainBackendUrl = process.env.MAIN_BACKEND_URL;
  if (mainBackendUrl) {
    try {
      await axios.post(`${mainBackendUrl}/calibration/mapping`, req.body);
      console.log('[Calibration] Forwarded mappings to main backend');
    } catch (err) {
      console.error('[Calibration] Failed to forward to main backend:', err.message);
    }
  }

  res.json({
    success: true,
    message: `Stored ${room_mapping.length} room mappings`,
    mappings_count: room_mapping.length
  });
});

/**
 * Complete calibration session
 */
app.post('/calibration/complete', async (req, res) => {
  const { meeting_id, meeting_uuid, success, total_rooms, mapped_rooms, completed_at } = req.body;

  if (!meeting_id) {
    return res.status(400).json({ error: 'Missing meeting_id' });
  }

  if (calibrationData.sessions[meeting_id]) {
    calibrationData.sessions[meeting_id].status = success ? 'completed' : 'failed';
    calibrationData.sessions[meeting_id].completed_at = completed_at;
    calibrationData.sessions[meeting_id].total_rooms = total_rooms;
    calibrationData.sessions[meeting_id].mapped_rooms = mapped_rooms;
  }

  console.log(`[Calibration] ${success ? 'Completed' : 'Failed'} for meeting ${meeting_id}`);
  console.log(`  Mapped ${mapped_rooms}/${total_rooms} rooms`);

  // Forward to main backend if configured
  const mainBackendUrl = process.env.MAIN_BACKEND_URL;
  if (mainBackendUrl) {
    try {
      await axios.post(`${mainBackendUrl}/calibration/complete`, req.body);
      console.log('[Calibration] Forwarded completion to main backend');
    } catch (err) {
      console.error('[Calibration] Failed to forward to main backend:', err.message);
    }
  }

  res.json({
    success: true,
    message: 'Calibration session completed'
  });
});

/**
 * Get existing mappings for a meeting
 */
app.get('/calibration/mappings/:meetingId', (req, res) => {
  const { meetingId } = req.params;

  const mappings = calibrationData.mappings[meetingId];

  if (!mappings) {
    return res.json({ mappings: [], count: 0 });
  }

  res.json({
    mappings: mappings.rooms,
    count: mappings.count,
    meeting_uuid: mappings.meeting_uuid,
    completed_at: mappings.completed_at
  });
});

/**
 * Get all calibration sessions (for debugging)
 */
app.get('/calibration/sessions', (req, res) => {
  res.json({
    sessions: calibrationData.sessions,
    mappings: Object.keys(calibrationData.mappings).map(id => ({
      meeting_id: id,
      room_count: calibrationData.mappings[id].count
    }))
  });
});

// =============================================================================
// Serve React App (Production)
// =============================================================================

// Serve React app for all other routes
if (process.env.NODE_ENV === 'production') {
  app.get('*', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
  });
}

// =============================================================================
// Start Server
// =============================================================================

app.listen(PORT, () => {
  console.log('========================================');
  console.log('Breakout Room Calibrator Server');
  console.log('========================================');
  console.log(`Port: ${PORT}`);
  console.log(`Mode: ${process.env.NODE_ENV || 'development'}`);
  console.log(`Health: http://localhost:${PORT}/health`);
  if (process.env.MAIN_BACKEND_URL) {
    console.log(`Main Backend: ${process.env.MAIN_BACKEND_URL}`);
  }
  console.log('========================================');
});
