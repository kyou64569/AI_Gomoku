import React from 'react';
import { useParams, Link } from 'react-router-dom';
import GameTable from '../components/GameTable';

export default function GamePage() {
  const { gameId } = useParams();
  return (
    <div className="min-h-screen bg-slate-950">
      <nav className="p-4 bg-slate-900 flex justify-between">
        <h1 className="text-xl font-bold">对局 #{gameId}</h1>
        <Link to="/" className="text-blue-400 hover:underline">返回首页</Link>
      </nav>
      <GameTable gameId={Number(gameId)} mode="pve" />
    </div>
  );
}
