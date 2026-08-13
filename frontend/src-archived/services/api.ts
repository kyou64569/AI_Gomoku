import axios from 'axios';
import type { ModelConfig, AIPlayer, Room, GameState } from '../types';

const api = axios.create({ baseURL: '/api' });

export const configApi = {
  list: () => api.get<ModelConfig[]>('/configs').then(r => r.data),
  create: (data: Omit<ModelConfig, 'id'>) => api.post<ModelConfig>('/configs/', data).then(r => r.data),
  test: (id: number) => api.post(`/configs/${id}/test`).then(r => r.data),
  fetchModels: (id: number) => api.post(`/configs/${id}/models`).then(r => r.data),
  delete: (id: number) => api.delete(`/configs/${id}`).then(r => r.data),
};

export const playerApi = {
  list: () => api.get<AIPlayer[]>('/players').then(r => r.data),
  create: (data: Omit<AIPlayer, 'id'>) => api.post<AIPlayer>('/players/', data).then(r => r.data),
  update: (id: number, data: Partial<AIPlayer>) => api.put(`/players/${id}`, data).then(r => r.data),
  delete: (id: number) => api.delete(`/players/${id}`).then(r => r.data),
};

export const roomApi = {
  create: (mode: 'pve' | 'watch', seats: { player_id: number; role: 'black' | 'white' }[]) =>
    api.post<Room>('/rooms/', { mode, seats }).then(r => r.data),
  list: () => api.get<Room[]>('/rooms').then(r => r.data),
  start: (id: number) => api.post(`/rooms/${id}/start`).then(r => r.data),
};

export const gameApi = {
  state: (id: number) => api.get<GameState>(`/games/${id}/state`).then(r => r.data),
  move: (id: number, row: number, col: number) => api.post(`/games/${id}/move`, { row, col }).then(r => r.data),
};
