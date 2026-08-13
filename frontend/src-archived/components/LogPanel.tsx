import React from 'react';

export default function LogPanel({ logs }: { logs: string[] }) {
  return (
    <div className="bg-slate-900 rounded p-3 h-96 overflow-y-auto">
      <h3 className="text-sm font-bold mb-2 text-slate-400">AI 思考日志</h3>
      {logs.length === 0 && <div className="text-slate-500 text-sm">暂无日志</div>}
      {logs.map((log, i) => (
        <div key={i} className="text-xs text-slate-300 mb-1 border-b border-slate-800 pb-1">{log}</div>
      ))}
    </div>
  );
}
