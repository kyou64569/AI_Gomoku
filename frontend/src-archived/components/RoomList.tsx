import React from 'react';

export default function RoomList({ rooms, onStart, onCreate }: { rooms: any[]; onStart: (id: number) => void; onCreate: () => void }) {
  return (
    <div className="p-4">
      <div className="flex justify-between items-center mb-4">
        <h2 className="text-xl font-bold">房间列表</h2>
        <button onClick={onCreate} className="px-4 py-2 bg-blue-600 rounded hover:bg-blue-500">创建房间</button>
      </div>
      <div className="space-y-2">
        {rooms.map(room => (
          <div key={room.id} className="p-3 bg-slate-800 rounded flex justify-between items-center">
            <div>
              <div className="font-semibold">房间 #{room.id} - {room.mode === 'pve' ? '人机' : '观战'}</div>
              <div className="text-sm text-slate-400">状态: {room.status}</div>
            </div>
            {room.status === 'waiting' && (
              <button onClick={() => onStart(room.id)} className="px-3 py-1 bg-green-600 rounded text-sm">开始</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
