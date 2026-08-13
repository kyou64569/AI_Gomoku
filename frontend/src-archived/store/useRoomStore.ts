import { create } from 'zustand';
import type { Room } from '../types';
import { roomApi } from '../services/api';

interface RoomState {
  rooms: Room[];
  loading: boolean;
  fetchRooms: () => Promise<void>;
  createRoom: (mode: 'pve' | 'watch', seats: { player_id: number; role: 'black' | 'white' }[]) => Promise<Room>;
  startRoom: (id: number) => Promise<void>;
}

export const useRoomStore = create<RoomState>((set, get) => ({
  rooms: [],
  loading: false,
  fetchRooms: async () => {
    set({ loading: true });
    const rooms = await roomApi.list();
    set({ rooms, loading: false });
  },
  createRoom: async (mode, seats) => {
    const room = await roomApi.create(mode, seats);
    set({ rooms: [...get().rooms, room] });
    return room;
  },
  startRoom: async (id) => {
    await roomApi.start(id);
    await get().fetchRooms();
  },
}));
