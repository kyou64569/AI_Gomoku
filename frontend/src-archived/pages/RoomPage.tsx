import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { roomApi } from '../services/api';

export default function RoomPage() {
  const { roomId } = useParams();
  const [starting, setStarting] = React.useState(false);

  const handleStart = async () => {
    setStarting(true);
    await roomApi.start(Number(roomId));
    window.location.href = `/game/${roomId}`;
  };

  return (
    <div className="min-h-screen bg-slate-950 p-4">
      <nav className="flex justify-between mb-4">
        <h1 className="text-xl font-bold">房间 #{roomId}</h1>
        <Link to="/" className="text-blue-400 hover:underline">返回首页</Link>
      </nav>
      <div className="bg-slate-900 p-6 rounded">
        <p className="mb-4">房间已创建，等待开始...</p>
        <button onClick={handleStart} disabled={starting} className="px-6 py-3 bg-green-600 rounded hover:bg-green-500">
          {starting ? '启动中...' : '开始游戏'}
        </button>
      </div>
    </div>
  );
}
