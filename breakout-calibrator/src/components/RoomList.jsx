import React from 'react';

function RoomList({ rooms, mappedRooms, currentRoom }) {
  if (!rooms || rooms.length === 0) {
    return (
      <div style={{
        padding: '20px',
        textAlign: 'center',
        color: '#666'
      }}>
        No breakout rooms discovered yet
      </div>
    );
  }

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      maxHeight: '300px',
      overflowY: 'auto',
      padding: '4px'
    }}>
      {rooms.map((room, index) => {
        const isMapped = mappedRooms.some(m => m.roomUUID === room.roomUUID);
        const isCurrent = currentRoom === index;

        return (
          <RoomItem
            key={room.roomUUID || index}
            room={room}
            index={index}
            isMapped={isMapped}
            isCurrent={isCurrent}
          />
        );
      })}
    </div>
  );
}

function RoomItem({ room, index, isMapped, isCurrent }) {
  const roomName = room.breakoutRoomName || room.name || `Room ${index + 1}`;
  const roomId = room.breakoutRoomUUID || room.uuid || '';
  const shortId = roomId.substring(0, 8);

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '12px 16px',
      backgroundColor: isCurrent
        ? 'rgba(45, 140, 255, 0.2)'
        : 'rgba(255, 255, 255, 0.05)',
      borderRadius: '8px',
      border: isCurrent
        ? '1px solid #2D8CFF'
        : '1px solid transparent',
      transition: 'all 0.2s ease'
    }}>
      {/* Room info */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px'
        }}>
          <span style={{
            color: '#fff',
            fontWeight: '500',
            fontSize: '14px'
          }}>
            {roomName}
          </span>
          {isCurrent && (
            <span style={{
              padding: '2px 8px',
              backgroundColor: '#2D8CFF',
              borderRadius: '12px',
              fontSize: '10px',
              color: '#fff',
              fontWeight: '600'
            }}>
              CURRENT
            </span>
          )}
        </div>
        <span style={{
          color: '#666',
          fontSize: '11px',
          fontFamily: 'monospace'
        }}>
          ID: {shortId}...
        </span>
      </div>

      {/* Status badge */}
      <div>
        {isMapped ? (
          <span style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            padding: '4px 12px',
            backgroundColor: 'rgba(0, 200, 81, 0.2)',
            color: '#00C851',
            borderRadius: '16px',
            fontSize: '12px',
            fontWeight: '500'
          }}>
            <span>✓</span>
            Mapped
          </span>
        ) : (
          <span style={{
            padding: '4px 12px',
            backgroundColor: 'rgba(255, 255, 255, 0.1)',
            color: '#888',
            borderRadius: '16px',
            fontSize: '12px'
          }}>
            Pending
          </span>
        )}
      </div>
    </div>
  );
}

export default RoomList;
