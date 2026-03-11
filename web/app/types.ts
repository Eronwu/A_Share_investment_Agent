/** Shared types — single source of truth */

export interface WatchEntry {
  name: string;
  type?: string;
  cost?: number | null;
  shares?: number | null;
  lot?: number | null;
  hidden?: boolean;
  star?: boolean;
  dip_buy?: boolean;
  tags?: string[];
  watch_price?: number;
  watch_price_date?: string;
}

export interface MonitorConfig {
  watchlist: Record<string, WatchEntry>;
  holdings?: Record<string, WatchEntry>;
  watching?: Record<string, WatchEntry>;
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
  star?: boolean;
  dip_buy?: boolean;
  tags?: string[];
  watch_price?: number;
  watch_price_date?: string;
  /* Futu L2 enrichment (optional — absent when OpenD offline or A-share no permission) */
  mainNetInflow?: number;
  mainNetInflowPct?: number;
  retailNetInflow?: number;
  bidAskRatio?: number;
  avgPrice?: number;
}

export interface AlertSettings {
  poll_interval?: number;
  big_move_pct?: number;
  cooldown_minutes?: number;
}

export interface MarketTurnover {
  sh: number;
  sz: number;
  total: number;
  shIndex: number;
  szIndex: number;
  shPct: number;
  szPct: number;
  verdict: string;
}

export interface AlertEvent {
  ts: number;
  time: string;
  symbol: string;
  kind: string;
  level?: number;
  message: string;
  display: string;
  change_pct: number;
}
