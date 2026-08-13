import React, { useEffect, useRef, useState } from 'react';
import Board from './Board';
import LogPanel from './LogPanel';
import { gameApi } from '../services/api';
import type { GameState } from '../types';

export default function GameTable({ gameId, mode }: { gameId: number; mode: 'pve' | 'watch' }) {
  const [state, setState] = useState<GameState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    const fetchState = async () => {
      try {
        const s = await gameApi.state(gameId);
        setState(s);
      } catch (e) {
        setError('获取状态失败');
      }
    };
    fetchState();
    const es = new EventSource(`/api/games/${gameId}/stream`);
    eventSourceRef.current = es;
    es.addEventListener('game_update', (e) => {
      const data = JSON.parse(e.data);
      setState(prev => prev ? { ...prev, ...data } : null);
    });
    es.addEventListener('game_over', (e) => {
      const data = JSON.parse(e.data);
      setState(prev => prev ? { ...prev, status: data.status, winner: data.winner } : null);
    });
    es.addEventListener('error', () => {
      setError('连接断开');
    });
    return () => es.close();
  }, [gameId]);

  const handleMove = async (row: number, col: number) => {
    if (mode === 'watch') return;
    try {
      await gameApi.move(gameId, row, col);
    } catch (e) {
      setError('落子失败');
    }
  };

  if (error) return <div className="p-4 text-red-400">{error}</div>;
  if (!state) return <div className="p-4">加载中...</div>;

  return (
    <div className="flex flex-col lg:flex-row gap-4 p-4">
      <div className="flex-1">
        <div className="flex justify-between mb-2">
          <div className="text-sm text-slate-400">黑方: {state.history[0]?.player_name || 'Player'}</div>
          <div className="text-sm text-slate-400">白方: {state.history[1]?.player_name || 'AI'}</div>
        </div>
        <Board board={state.board} onCellClick={handleMove} disabled={mode === 'watch' || state.turn !== 1 || state.status !== 'playing'} />
        {state.status === 'finished' && (
          <div className="mt-4 text-center text-xl font-bold">
            {state.winner === 1 ? '黑方获胜！' : state.winner === 2 ? '白方获胜！' : '平局'}
          </div>
        )}
      </div>
      <div className="w-full lg:w-80">
        <LogPanel logs={state.logs} />
      </div>
    </div>
  );
}
