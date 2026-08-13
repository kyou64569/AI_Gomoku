import { create } from 'zustand';
import type { ModelConfig, AIPlayer } from '../types';
import { configApi, playerApi } from '../services/api';

interface ConfigState {
  configs: ModelConfig[];
  players: AIPlayer[];
  loading: boolean;
  fetchConfigs: () => Promise<void>;
  createConfig: (data: Omit<ModelConfig, 'id'>) => Promise<void>;
  testConfig: (id: number) => Promise<{ status: string; detail: string }>;
  fetchModels: (id: number) => Promise<string[]>;
  deleteConfig: (id: number) => Promise<void>;
  fetchPlayers: () => Promise<void>;
  createPlayer: (data: Omit<AIPlayer, 'id'>) => Promise<void>;
  updatePlayer: (id: number, data: Partial<AIPlayer>) => Promise<void>;
  deletePlayer: (id: number) => Promise<void>;
}

export const useConfigStore = create<ConfigState>((set, get) => ({
  configs: [],
  players: [],
  loading: false,
  fetchConfigs: async () => {
    set({ loading: true });
    const configs = await configApi.list();
    const players = await playerApi.list();
    set({ configs, players, loading: false });
  },
  createConfig: async (data) => {
    await configApi.create(data);
    await get().fetchConfigs();
  },
  testConfig: async (id) => {
    return await configApi.test(id);
  },
  fetchModels: async (id) => {
    const res = await configApi.fetchModels(id);
    return res.models;
  },
  deleteConfig: async (id) => {
    await configApi.delete(id);
    await get().fetchConfigs();
  },
  fetchPlayers: async () => {
    const players = await playerApi.list();
    set({ players });
  },
  createPlayer: async (data) => {
    await playerApi.create(data);
    await get().fetchPlayers();
  },
  updatePlayer: async (id, data) => {
    await playerApi.update(id, data);
    await get().fetchPlayers();
  },
  deletePlayer: async (id) => {
    await playerApi.delete(id);
    await get().fetchPlayers();
  },
}));
