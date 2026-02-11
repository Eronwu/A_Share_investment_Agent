/** Shared types — single source of truth */

export interface WatchEntry {
  name: string;
  type?: string;
  cost?: number | null;
  shares?: number | null;
  hidden?: boolean;
}

export interface MonitorConfig {
  watchlist: Record<string, WatchEntry>;
  settings: Record<string, number>;
}

export interface Service {
  id: string;
  name: string;
  type: string;
  price: number;
  change: number;
  chgAmt: number;
  vol: number;
  amount: number;
  amp: number;
  turnover: number;
  volRatio: number;
  high: number;
  low: number;
  open: number;
  prevClose: number;
  cost: number | null;
  shares: number | null;
  pnl: number | null;
  above: number | null;
  below: number | null;
  hidden?: boolean;
}

export interface AlertSettings {
  poll_interval?: number;
  big_move_pct?: number;
  cooldown_minutes?: number;
}
