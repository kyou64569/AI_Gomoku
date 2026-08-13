import React from 'react';
import type { AIPlayer } from '../types';

export default function PlayerSelect({ label, players, selected, onChange }: { label: string; players: AIPlayer[]; selected: number; onChange: (id: number) => void }) {
  return (
    <div className="mb-2">
      <label className="block text-sm text-slate-400 mb-1">{label}</label>
      <select className="w-full p-2 bg-slate-800 rounded" value={selected} onChange={e => onChange(Number(e.target.value))}>
        <option value={0}>选择玩家</option>
        {players.map(p => (
          <option key={p.id} value={p.id}>{p.name} ({p.model_id})</option>
        ))}
      </select>
    </div>
  );
}
