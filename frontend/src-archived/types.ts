export interface ModelConfig {
  id: number;
  name: string;
  base_url: string;
  api_key: string;
  models: string[];
}

export interface AIPlayer {
  id: number;
  name: string;
  model_config_id: number;
  model_id: string;
  temperature: number;
}

export interface Room {
  id: number;
  mode: 'pve' | 'watch';
  seats: Seat[];
  status: string;
}

export interface Seat {
  player_id: number | null | string;
  role: 'black' | 'white';
}

export interface GameState {
  id: number;
  board: number[][];
  turn: number;
  history: Move[];
  winner: number;
  logs: string[];
  status: string;
  scores: { black: number; white: number };
}

export interface Move {
  player: number;
  player_name: string;
  row: number;
  col: number;
}
