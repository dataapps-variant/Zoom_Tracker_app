import React from 'react';

const STATUS_STYLES = {
  idle: { color: '#888', icon: '○' },
  checking: { color: '#2D8CFF', icon: '◉' },
  calibrating: { color: '#FFB800', icon: '◉' },
  complete: { color: '#00C851', icon: '✓' },
  error: { color: '#FF4444', icon: '✕' }
};

function StatusMessage({ status, message }) {
  const style = STATUS_STYLES[status] || STATUS_STYLES.idle;

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: '8px',
      padding: '12px 16px',
      backgroundColor: 'rgba(255,255,255,0.05)',
      borderRadius: '8px',
      borderLeft: `3px solid ${style.color}`
    }}>
      <span style={{
        color: style.color,
        fontSize: '16px',
        fontWeight: 'bold'
      }}>
        {style.icon}
      </span>
      <span style={{ color: '#fff', fontSize: '14px' }}>
        {message || getDefaultMessage(status)}
      </span>
    </div>
  );
}

function getDefaultMessage(status) {
  switch (status) {
    case 'idle':
      return 'Ready to start calibration';
    case 'checking':
      return 'Checking meeting status...';
    case 'calibrating':
      return 'Calibration in progress...';
    case 'complete':
      return 'Calibration complete!';
    case 'error':
      return 'An error occurred';
    default:
      return '';
  }
}

export default StatusMessage;
