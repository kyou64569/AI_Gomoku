import React, { useState, useEffect } from 'react';
import { useConfigStore } from '../store/useConfigStore';
import { configApi } from '../services/api';

export default function ConfigPanel() {
  const { configs, players, fetchConfigs, createConfig, testConfig, fetchModels, deleteConfig, createPlayer, deletePlayer } = useConfigStore();
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [playerName, setPlayerName] = useState('');
  const [modelId, setModelId] = useState('');
  const [temperature, setTemperature] = useState(70);

  useEffect(() => { fetchConfigs(); }, [fetchConfigs]);

  const handleCreateConfig = async () => {
    if (!name || !baseUrl || !apiKey) return;
    await createConfig({ name, base_url: baseUrl, api_key: apiKey, models: [] });
    setName(''); setBaseUrl(''); setApiKey('');
  };

  const handleFetchModels = async (id: number) => {
    const list = await fetchModels(id);
    setModels(list);
    setSelectedId(id);
  };

  const handleCreatePlayer = async () => {
    if (!playerName || !selectedId || !modelId) return;
    await createPlayer({ name: playerName, model_config_id: selectedId, model_id: modelId, temperature });
    setPlayerName(''); setModelId('');
  };

  return (
    <div className="p-4 space-y-6">
      <section>
        <h3 className="text-lg font-bold mb-2">模型配置</h3>
        <div className="space-y-2">
          <input className="w-full p-2 bg-slate-800 rounded" placeholder="配置名称" value={name} onChange={e => setName(e.target.value)} />
          <input className="w-full p-2 bg-slate-800 rounded" placeholder="Base URL" value={baseUrl} onChange={e => setBaseUrl(e.target.value)} />
          <input className="w-full p-2 bg-slate-800 rounded" placeholder="API Key" type="password" value={apiKey} onChange={e => setApiKey(e.target.value)} />
          <button onClick={handleCreateConfig} className="px-4 py-2 bg-blue-600 rounded">添加配置</button>
        </div>
        <div className="mt-4 space-y-2">
          {configs.map(c => (
            <div key={c.id} className="p-3 bg-slate-800 rounded flex justify-between items-center">
              <div>
                <div className="font-semibold">{c.name}</div>
                <div className="text-xs text-slate-400">{c.base_url}</div>
              </div>
              <div className="space-x-2">
                <button onClick={() => testConfig(c.id)} className="px-2 py-1 bg-slate-600 rounded text-sm">测试</button>
                <button onClick={() => handleFetchModels(c.id)} className="px-2 py-1 bg-slate-600 rounded text-sm">拉取模型</button>
                <button onClick={() => deleteConfig(c.id)} className="px-2 py-1 bg-red-600 rounded text-sm">删除</button>
              </div>
            </div>
          ))}
        </div>
        {selectedId && (
          <div className="mt-2 p-2 bg-slate-900 rounded">
            <div className="text-sm font-semibold mb-1">可用模型</div>
            <select className="w-full p-2 bg-slate-800 rounded" value={modelId} onChange={e => setModelId(e.target.value)}>
              <option value="">选择模型</option>
              {models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        )}
      </section>

      <section>
        <h3 className="text-lg font-bold mb-2">AI 玩家</h3>
        <div className="space-y-2">
          <input className="w-full p-2 bg-slate-800 rounded" placeholder="玩家名称" value={playerName} onChange={e => setPlayerName(e.target.value)} />
          <input className="w-full p-2 bg-slate-800 rounded" placeholder="温度 (0-100)" type="number" value={temperature} onChange={e => setTemperature(Number(e.target.value))} />
          <button onClick={handleCreatePlayer} className="px-4 py-2 bg-blue-600 rounded">添加玩家</button>
        </div>
        <div className="mt-4 space-y-2">
          {players.map(p => (
            <div key={p.id} className="p-3 bg-slate-800 rounded flex justify-between items-center">
              <div>
                <div className="font-semibold">{p.name}</div>
                <div className="text-xs text-slate-400">模型: {p.model_id} | 温度: {p.temperature}</div>
              </div>
              <button onClick={() => deletePlayer(p.id)} className="px-2 py-1 bg-red-600 rounded text-sm">删除</button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
