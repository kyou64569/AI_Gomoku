import React from 'react';
import { Link } from 'react-router-dom';
import ConfigPanel from '../components/ConfigPanel';

export default function ConfigPage() {
  return (
    <div className="min-h-screen bg-slate-950">
      <nav className="p-4 bg-slate-900 flex justify-between">
        <h1 className="text-xl font-bold">AI Gomoku - 配置管理</h1>
        <Link to="/" className="text-blue-400 hover:underline">返回首页</Link>
      </nav>
      <ConfigPanel />
    </div>
  );
}
