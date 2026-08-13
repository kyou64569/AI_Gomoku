import React from 'react';
import { Link } from 'react-router-dom';
import RoomList from '../components/RoomList';
import { useRoomStore } from '../store/useRoomStore';

export default function Home() {
  const { rooms, fetchRooms, createRoom, startRoom } = useRoomStore();
  const [mode, setMode] = React.useState<'pve' | 'watch'>('pve');
  const [black, setBlack] = React.useState(0);
  const [white, setWhite] = React.useState(0);

  React.useEffect(() => { fetchRooms(); }, [fetchRooms]);

  const handleCreate = async () => {
    if (!black || !white) return alert('请选择两位AI玩家');
    await createRoom(mode, [
      { player_id: black, role: 'black' },
      { player_id: white, role: 'white' }
    ]);
    fetchRooms();
  };

  return (
    <div className="min-h-screen bg-slate-950">
      <nav className="p-4 bg-slate-900 flex justify-between">
        <h1 className="text-xl font-bold">AI Gomoku</h1>
        <Link to="/config" className="text-blue-400 hover:underline">配置管理</Link>
      </nav>
      <div className="p-4">
        <div className="bg-slate-900 p-4 rounded mb-6">
          <h2 className="text-lg font-bold mb-2">创建房间</h2>
          <div className="flex gap-4 mb-2">
            <button onClick={() => setMode('pve')} className={`px-4 py-2 rounded ${mode==='pve'?'bg-blue-600':'bg-slate-800'}`}>人机模式</button>
            <button onClick={() => setMode('watch')} className={`px-4 py-2 rounded ${mode==='watch'?'bg-blue-600':'bg-slate-800'}`}>观战模式</button>
          </div>
          <div className="flex gap-4 items-end">
            <PlayerSelectWrapper label="黑方" value={black} onChange={setBlack} />
            <PlayerSelectWrapper label="白方" value={white} onChange={setWhite} />
            <button onClick={handleCreate} className="px-4 py-2 bg-green-600 rounded">创建</button>
          </div>
        </div>
        <RoomList rooms={rooms} onStart={(id) => startRoom(id)} onCreate={handleCreate} />
      </div>
    </div>
  );
}

function PlayerSelectWrapper({ label, value, onChange }: { label: string; value: number; onChange: (id: number) => void }) {
  const { players } = useRoomStore as any;
  return (
    <div>
      <label className="block text-sm text-slate-400 mb-1">{label}</label>
      <select className="p-2 bg-slate-800 rounded" value={value} onChange={e => onChange(Number(e.target.value))}>
        <option value={0}>选择玩家</option>
        {/* 这里应使用 players store，简化处理 */}
      </select>
    </div>
  );
}
