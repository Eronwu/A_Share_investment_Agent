# 会话上下文 - 股票盯盘提醒系统

## 已完成

### 新建文件: `src/tools/stock_monitor.py` (~300行)

macOS 股票盯盘提醒系统，基于新浪财经实时行情 API（非 akshare，避免限流）。

**核心模块:**
- `fetch_realtime_sina()` - 批量获取实时行情（单次 HTTP 请求）
- `notify()` - macOS osascript 原生通知
- `AlertEngine` - 告警引擎（价格阈值 + 大涨大跌 + 冷却机制）
- `monitor_loop()` - 主循环（交易时段检测 + 轮询 + 终端状态行）
- CLI: `--add/--remove/--list/--interval/--threshold`

**配置文件:** `src/data/monitor_config.json`（已生成，含 11 只 AIDC watchlist 股票）

**依赖:** 仅 `requests`（已有）+ 标准库，无需改 pyproject.toml

**复用:** `get_stock_prefix()`, `AIDC_WATCHLIST`, `setup_logger()`

## 已验证通过
- `--help` 正常输出
- `--add 688676 --above 100 --below 85` 设置价格提醒
- `--list` 显示监控列表
- `--remove 688676` 删除提醒
- Sina API 批量获取 11 只股票 <500ms
- macOS 通知弹窗正常
- 科华数据 +3.8% 自动触发大涨告警

## 使用方式
```bash
poetry run python src/tools/stock_monitor.py              # 启动监控
poetry run python src/tools/stock_monitor.py --list        # 查看设置
poetry run python src/tools/stock_monitor.py --add 688676 --above 100 --below 85
poetry run python src/tools/stock_monitor.py --remove 688676
poetry run python src/tools/stock_monitor.py --interval 15 --threshold 3.0
```
