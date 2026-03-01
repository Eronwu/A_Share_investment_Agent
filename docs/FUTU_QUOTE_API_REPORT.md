# Futu Quote API — Data Capabilities Report for A-Share / HK Monitoring & Simulated Trading

**Source**: [Futu API Quote Documentation v9.6](https://openapi.futunn.com/futu-api-doc/quote/overview.html)  
**Audience**: A-share + HK dashboard, alerting, simulated trading, market breadth, daily summary analytics, risk controls

---

## 1. Structured Endpoint Summary

### 1.1 Subscription Management

| Endpoint | Data provided | Subscription / live required | Suggested use | Priority | Caveats |
|----------|---------------|-----------------------------|---------------|----------|---------|
| **subscribe** | Registers real-time data push for specified stocks + SubTypes (QUOTE, ORDER_Book, TICKER, RT_DATA, K_DAY, etc.) | Yes — establishes live connection; HK/US need LV1+ (BMP unsupported) | Poller / Notifier / Sim engine: subscribe watchlist for QUOTE + RT_DATA | P0 | Each stock×SubType uses 1 quota; default ~1000 total; HK SF ORDER_BOOK limited to 50 securities; ≥1 min before unsubscribe |
| **unsubscribe** | Cancels specific stock×SubType subscriptions | N/A | Poller / Sim engine: cleanup when removing from watchlist | P0 | Must wait ≥1 min after subscribe |
| **unsubscribe_all** | Cancels all subscriptions on connection | N/A | Poller: graceful shutdown | P2 | Same 1-min rule |
| **query_subscription** | `total_used`, `remain`, `own_used`, `sub_list` (per SubType → code list) | No | Poller / Web: health check, quota monitoring | P1 | — |

---

### 1.2 Core Real-time / Snapshot APIs

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **get_market_snapshot** | price, volume, turnover, turnover_rate, suspension, OHLC, prev_close, bid/ask, volume_ratio, amplitude, market_val, PE/PB, lot_size, index/plate raise/fall counts; up to 400 codes per request | No | Poller: primary source for dashboard + alerting (replace/ supplement 东方财富) | P0 | Max 400 codes per call; covers A-share/HK/US |
| **get_stock_quote** | last_price, open/high/low, volume, turnover, turnover_rate, amplitude, suspension, spread | Yes — subscribed stocks only | Sim engine / Notifier: lower-latency quote when already subscribed | P1 | Requires prior subscribe |
| **get_order_book** | Bid/Ask (price, volume, order_num), svr_recv_time | Yes — subscribed stocks only | Sim engine / L2 strategy: order flow, imbalance | P1 | Detailed orderbook only on HK SF; LV2 US/美期 no detailed orderbook |
| **get_rt_data** | time, opened_mins, cur_price, last_close, avg_price, volume, turnover | Yes — subscribed | Sim engine / Daily summary: intraday VWAP, price path | P2 | Per-code subscription |
| **get_rt_ticker** | sequence, time, price, volume, turnover, ticker_direction, type | Yes — subscribed | Sim engine / L2: tick-level sentiment, imbalance | P2 | Up to 1000 ticks per call |
| **get_cur_kline** | time_key, O/H/L/C, volume, turnover, turnover_rate, last_close; up to 1000 bars | Yes — subscribed | Sim engine / DailyIndicatorTracker: live day bar | P2 | Requires subscribe for K_DAY |

---

### 1.3 Market State & Calendar

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **get_market_state** | code, stock_name, market_state (MORNING, AFTERNOON, CLOSED, etc.) | No | Poller / Sim engine: trading window, risk controls | P0 | Per-code or code list |
| **get_global_state** | market_sz, market_sh, market_hk, market_us, market_hkfuture, qot_logined, trd_logined, timestamp | No | Poller / Web: session state, trading-day detection | P0 | No subscription needed |
| **request_trading_days** | time, trade_date_type (WHOLE/HALF/MORNING/ etc.); market or code-specific | No | Sector engine / Poller: skip weekend logic, backfill | P0 | Excludes临时休市 |

---

### 1.4 History & K-line

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **request_history_kline** | time_key, open, close, high, low, volume, turnover, turnover_rate, change_rate, last_close, pe_ratio | No | Sim engine / DailyIndicatorTracker: RSI, MACD, MA, ATR; 120-day lookback | P0 | Pagination for >1000 bars; quota per stock per cycle |
| **get_cur_kline** | Same as above, live last N bars | Yes — subscribed | Sim engine: real-time day bar | P2 | See above |
| **get_history_kl_quota** | used_quota, remain_quota, detail_list (code, request_time) | No | Poller / Sim engine: avoid overuse | P1 | 100 stocks/cycle (typical); cycle documented in authority page |

---

### 1.5 Capital Flow & Distribution

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **get_capital_flow** | in_flow, main_in_flow, super/big/mid/sml_in_flow, capital_flow_item_time, last_valid_time; PeriodType: INTRADAY, DAY, WEEK, MONTH | No | Sim engine / Daily summary: main net inflow, institutional vs retail | P0 | main_in_flow only for non-realtime periods |
| **get_capital_distribution** | capital_in/out_super, big, mid, small; update_time | No | Daily summary / L2 digest: large-order flow breakdown | P1 | Single snapshot per call |

---

### 1.6 Sector / Plate / Breadth

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **get_owner_plate** | code, name, plate_code, plate_name, plate_type (INDUSTRY, CONCEPT, OTHER) | No | Sector / Web: stock→plate mapping, rotation context | P0 | Supports stock + index |
| **get_plate_list** | code, plate_name, plate_id; by market + plate_class | No | Sector engine: list plates before get_plate_stock | P0 | HK/SH/SZ markets |
| **get_plate_stock** | code, stock_name, lot_size, stock_type, list_time | No | Sector engine: components for custom indices | P0 | Use plate_code from get_plate_list |

---

### 1.7 Static / Basic Info

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **get_stock_basicinfo** | code, name, lot_size, stock_type, suspension, listing_date, stock_id, delisting, exchange_type | No | Web / Sim engine: lot_sizes, names, suspension | P0 | Full market or code_list |

---

### 1.8 Personalized / Alert-related APIs

| Endpoint | Data provided (key fields) | Subscription / live required | Suggested use | Priority | Caveats |
|----------|----------------------------|-----------------------------|---------------|----------|---------|
| **set_price_reminder** | Add/del/modify/enable/disable price reminders; op, key, reminder_type, value, note | No | Optional: sync above/below alerts with Futu OpenD | P2 | Requires OpenD; 20-char note limit |
| **get_price_reminder** | List reminders for stock/market | No | Optional: sync with alert_config | P2 | — |
| **PriceReminderHandlerBase** | Push when price hits reminder | Yes — handler registration | Optional: native Futu alerts | P2 | Overlap with our notifier |
| **get_user_security** | Watchlist securities in specified group | No | Optional: mirror Futu watchlist | P2 | Requires Futu account |
| **get_user_security_group** | List of watchlist groups | No | Optional: integrate with monitor_config | P2 | — |
| **modify_user_security** | Add/remove securities in group | No | Optional: bidir sync | P2 | — |

---

## 2. Top 10 Data Points to Ingest ( tailored for Chinese A-share + HK Dashboard, Alerts, Sim Trading )

1. **Market snapshot (price/OHLC/volume/turnover)** — `get_market_snapshot`  
   - One call for up to 400 codes; no subscription.  
   - Use: poller → market_data.json → dashboard + notifier thresholds.

2. **Global & per-code market state** — `get_global_state` + `get_market_state`  
   - Use: trading window, skip non-trading days, risk controls.

3. **History K-line** — `request_history_kline`  
   - Use: DailyIndicatorTracker (RSI/MACD/MA/ATR), sim scoring, backtest.

4. **Capital flow (intraday + daily)** — `get_capital_flow`  
   - main_in_flow, super/big/mid/sml breakdown.  
   - Use: daily summary, L2 digest, sim scoring.

5. **Plate / sector lists & constituents** — `get_plate_list` + `get_plate_stock` + `get_owner_plate`  
   - Use: sector rotation, custom indices (磷化工 etc.), breadth.

6. **Stock basic info (lot_size, name, suspension)** — `get_stock_basicinfo`  
   - Use: web display, sim lot-size alignment.

7. **Trading days calendar** — `request_trading_days`  
   - Use: sector_index_engine skip-weekend, backfill date ranges.

8. **Capital distribution** — `get_capital_distribution`  
   - Use: daily summary, large-order inflow/outflow breakdown.

9. **Real-time quote (when subscribed)** — `get_stock_quote`  
   - Use: sim engine if using push model; lower latency than snapshot for subscribed codes.

10. **History K-line quota** — `get_history_kl_quota`  
    - Use: manage request cadence, avoid quota exhaustion.

---

## 3. Suggested Integration Map

| Component | Primary APIs | Notes |
|-----------|--------------|-------|
| **Poller** | get_market_snapshot, get_global_state, get_market_state | Batch 400 codes; no subscribe needed |
| **Notifier** | Consumes market_data from poller | DeltaAlertEngine unchanged; optional sync with set_price_reminder |
| **Sim engine** | request_history_kline, get_capital_flow, get_capital_distribution, get_market_snapshot | DailyIndicatorTracker + L2 digest |
| **Sector engine** | get_plate_list, get_plate_stock, get_owner_plate, request_trading_days | Replace/supplement akshare/sina |
| **Web / API** | get_market_snapshot, get_stock_basicinfo | Read from poller output; no direct Futu calls in web layer |
| **Risk controls** | get_market_state, get_global_state | Trading window, max position, session checks |

---

## 4. Permission & Rate Summary

- **Subscription**: LV1+ required for HK/US; BMP unsupported. Each stock×SubType = 1 quota; default ~1000.  
- **Market snapshot**: No subscription; max 400 codes per request.  
- **History K-line**: Quota per stock per cycle (typically ~100 stocks); use `get_history_kl_quota`.  
- **HK SF ORDER_BOOK**: Max 50 securities for detailed orderbook.  
- **Unsubscribe**: ≥1 minute after subscribe.

---

*Report generated from Futu API Quote documentation v9.6. Verify quotas and permission rules against latest authority/intro docs.*
