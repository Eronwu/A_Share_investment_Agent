#!/usr/bin/env python3
"""
macOS 股票盯盘提醒系统

通过新浪财经 / 东方财富实时行情 API 定期获取股票数据，
当触发价格告警或大涨大跌条件时，通过 macOS 原生通知提醒。

用法:
    # 启动监控（使用默认 AIDC watchlist）
    poetry run python src/tools/stock_monitor.py

    # 实时行情看板（含量比/换手率，每分钟刷新）
    poetry run python src/tools/stock_monitor.py --realtime

    # 添加价格提醒
    poetry run python src/tools/stock_monitor.py --add 688676 --above 100 --below 85

    # 查看当前提醒设置
    poetry run python src/tools/stock_monitor.py --list

    # 删除某只股票的提醒
    poetry run python src/tools/stock_monitor.py --remove 688676

    # 调整轮询间隔和大涨大跌阈值
    poetry run python src/tools/stock_monitor.py --interval 15 --threshold 3.0
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 让 import 能找到项目模块
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.api import get_stock_prefix
from src.tools.stock_data_fetcher import AIDC_WATCHLIST

EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
from src.utils.logging_config import setup_logger

logger = setup_logger("stock_monitor")

# ── 配置路径 ──
CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"
ALERT_CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "alert_config.json"

# ── 默认配置 ──
DEFAULT_SETTINGS = {
    "poll_interval": 30,
    "big_move_pct": 3.0,
    "cooldown_minutes": 10,
}


# ══════════════════════════════════════════
# 1. 配置管理
# ══════════════════════════════════════════

def load_config() -> dict:
    """加载配置文件，不存在则用默认 AIDC watchlist 初始化"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    # 初始化默认配置
    config = {
        "watchlist": {
            code: {"name": name}
            for code, name in AIDC_WATCHLIST.items()
        },
        "settings": DEFAULT_SETTINGS.copy(),
    }
    save_config(config)
    return config


def save_config(config: dict):
    """保存配置到 JSON 文件（原子写入：tmp → rename）"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    tmp.replace(CONFIG_PATH)


def load_alerts() -> dict:
    """加载告警配置 alert_config.json"""
    if ALERT_CONFIG_PATH.exists():
        with open(ALERT_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("alerts", {})
    return {}


def save_alerts(alerts: dict):
    """保存告警配置（原子写入：tmp → rename）"""
    ALERT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ALERT_CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"alerts": alerts}, f, ensure_ascii=False, indent=2)
    tmp.replace(ALERT_CONFIG_PATH)


# ══════════════════════════════════════════
# 1b. 港股 / A股 识别工具
# ══════════════════════════════════════════

def is_hk_symbol(symbol: str) -> bool:
    """判断是否为港股代码（以 HK 开头，如 HK00700）"""
    return symbol.upper().startswith("HK")


def hk_code(symbol: str) -> str:
    """提取港股纯数字代码，如 HK00700 -> 00700"""
    return symbol.upper().removeprefix("HK")


def _sina_code(symbol: str) -> str:
    """转换为新浪格式: A股 sh600089/sz002335, 港股 hk00700"""
    if is_hk_symbol(symbol):
        return f"hk{hk_code(symbol)}"
    return f"{get_stock_prefix(symbol)}{symbol}"


def _em_secid(symbol: str) -> str:
    """转换为东方财富 secid: A股 1.600089/0.002335, 港股 116.00700"""
    if is_hk_symbol(symbol):
        return f"116.{hk_code(symbol)}"
    return f"{_em_market(symbol)}.{symbol}"


# ══════════════════════════════════════════
# 2. 新浪实时行情 API
# ══════════════════════════════════════════

SINA_API_URL = "https://hq.sinajs.cn/list="
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def fetch_realtime_sina(symbols: list[str]) -> dict:
    """一次 HTTP 请求批量获取所有股票实时行情

    Args:
        symbols: 股票代码列表，如 ["688676", "002335"]

    Returns:
        {symbol: {name, price, change_pct, open, high, low, volume, amount, prev_close}}
        获取失败的股票不会出现在结果中
    """
    if not symbols:
        return {}

    # 构造新浪格式的代码列表: sh600089,sz002335,hk00700,...
    sina_codes = [_sina_code(s) for s in symbols]
    url = SINA_API_URL + ",".join(sina_codes)

    try:
        resp = requests.get(url, headers=SINA_HEADERS, timeout=10)
        resp.encoding = "gbk"
    except requests.RequestException as e:
        logger.error(f"请求新浪行情失败: {e}")
        return {}

    quotes = {}
    for line in resp.text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = re.match(r'var hq_str_(\w+)="(.*)";', line)
        if not match:
            continue
        full_code = match.group(1)  # sh600089 / hk00700
        data_str = match.group(2)
        if not data_str:
            continue

        fields = data_str.split(",")

        try:
            if full_code.startswith("hk"):
                # ── 港股: 19 个字段 ──
                if len(fields) < 13:
                    continue
                symbol = f"HK{full_code[2:]}"
                name = fields[1]
                open_price = float(fields[2]) if fields[2] else 0
                prev_close = float(fields[3]) if fields[3] else 0
                high = float(fields[4]) if fields[4] else 0
                low = float(fields[5]) if fields[5] else 0
                price = float(fields[6]) if fields[6] else 0
                change_pct = float(fields[8]) if fields[8] else 0
                volume = int(float(fields[12])) if fields[12] else 0
                amount = float(fields[11]) if fields[11] else 0
            else:
                # ── A股: 32+ 个字段 ──
                if len(fields) < 32:
                    continue
                symbol = full_code[2:]
                name = fields[0]
                open_price = float(fields[1]) if fields[1] else 0
                prev_close = float(fields[2]) if fields[2] else 0
                price = float(fields[3]) if fields[3] else 0
                high = float(fields[4]) if fields[4] else 0
                low = float(fields[5]) if fields[5] else 0
                volume = int(float(fields[8])) if fields[8] else 0
                amount = float(fields[9]) if fields[9] else 0
                if prev_close > 0 and price > 0:
                    change_pct = round((price - prev_close) / prev_close * 100, 2)
                else:
                    change_pct = 0.0

            quotes[symbol] = {
                "name": name,
                "price": price,
                "change_pct": round(change_pct, 2),
                "open": open_price,
                "high": high,
                "low": low,
                "prev_close": prev_close,
                "volume": volume,
                "amount": amount,
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"解析 {full_code} 行情数据出错: {e}")
            continue

    return quotes


# ══════════════════════════════════════════
# 2b. 东方财富实时行情 API（含量比/换手率）
# ══════════════════════════════════════════

EASTMONEY_API_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
EASTMONEY_FIELDS = "f12,f14,f2,f3,f4,f5,f6,f7,f8,f10,f15,f16,f17,f18"


def _em_market(code: str) -> str:
    """东方财富市场编码: 沪市=1, 深市=0"""
    return "1" if code.startswith("6") else "0"


def fetch_realtime_eastmoney(symbols: list[str]) -> list[dict]:
    """通过东方财富 API 批量获取实时行情（含量比/换手率）

    单次 HTTP 请求，无限流风险。

    Returns:
        list of dict, 按原始顺序返回，每项包含:
        code, name, price, change, pct, volume, amount,
        amplitude, turnover, vol_ratio, high, low, open, prev_close
    """
    if not symbols:
        return []

    secids = ",".join(_em_secid(s) for s in symbols)
    params = {
        "fltt": "2",
        "secids": secids,
        "fields": EASTMONEY_FIELDS,
        "ut": EM_UT,
    }

    try:
        resp = requests.get(EASTMONEY_API_URL, params=params, timeout=10)
        data = resp.json()
    except Exception as e:
        logger.error(f"请求东方财富行情失败: {e}")
        return []

    if not data.get("data") or not data["data"].get("diff"):
        logger.warning("东方财富返回数据为空")
        return []

    # 纯数字代码 -> 原始 symbol 的映射 (处理港股 HK 前缀)
    code_map = {}
    for s in symbols:
        raw = hk_code(s) if is_hk_symbol(s) else s
        code_map[raw] = s

    results = []
    for s in data["data"]["diff"]:
        raw_code = s.get("f12", "")
        code = code_map.get(raw_code, raw_code)
        results.append({
            "code": code,
            "name": s.get("f14", ""),
            "price": s.get("f2", 0),
            "pct": s.get("f3", 0),
            "change": s.get("f4", 0),
            "volume": s.get("f5", 0),       # 成交量(手)
            "amount": s.get("f6", 0),        # 成交额(元)
            "amplitude": s.get("f7", 0),     # 振幅%
            "turnover": s.get("f8", 0),      # 换手率%
            "vol_ratio": s.get("f10", 0),    # 量比
            "high": s.get("f15", 0),
            "low": s.get("f16", 0),
            "open": s.get("f17", 0),
            "prev_close": s.get("f18", 0),
        })

    return results


# ══════════════════════════════════════════
# 2c. 实时行情看板（rich 表格）
# ══════════════════════════════════════════

def _render_realtime_board(stocks: list[dict], tick: int, watchlist: dict = None):
    """用 rich 渲染实时行情表格，持仓股显示盈亏"""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console()
    watchlist = watchlist or {}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"实时行情看板  |  {now}  |  第 {tick} 次刷新"

    # 检查是否有持仓
    has_holdings = any(
        watchlist.get(s.get("code", ""), {}).get("type") == "holding"
        for s in stocks
    )

    console_width = console.width or 120
    table = Table(
        title=title, show_lines=True, title_style="bold cyan",
        width=min(console_width, 150), expand=True,
    )
    table.add_column("", min_width=2, no_wrap=True)  # 持仓/自选标记
    table.add_column("代码", style="bold", min_width=6, no_wrap=True)
    table.add_column("名称", style="bold", min_width=6, no_wrap=True)
    table.add_column("现价", justify="right", min_width=7, no_wrap=True)
    table.add_column("涨跌幅", justify="right", min_width=8, no_wrap=True)
    table.add_column("涨跌", justify="right", min_width=6, no_wrap=True)
    if has_holdings:
        table.add_column("盈亏%", justify="right", min_width=7, no_wrap=True)
    table.add_column("量比", justify="right", min_width=5, no_wrap=True)
    table.add_column("换手%", justify="right", min_width=6, no_wrap=True)
    table.add_column("振幅%", justify="right", min_width=6, no_wrap=True)
    table.add_column("成交量(手)", justify="right", min_width=10, no_wrap=True)
    table.add_column("成交额(万)", justify="right", min_width=10, no_wrap=True)
    table.add_column("最高", justify="right", min_width=7, no_wrap=True)
    table.add_column("最低", justify="right", min_width=7, no_wrap=True)

    # 分组: 持仓优先，各组内按涨跌幅排序
    def sort_key(s):
        code = s.get("code", "")
        is_hold = watchlist.get(code, {}).get("type") == "holding"
        return (0 if is_hold else 1, -s.get("pct", 0))

    stocks_sorted = sorted(stocks, key=sort_key)

    for s in stocks_sorted:
        code = s.get("code", "")
        entry = watchlist.get(code, {})
        is_hold = entry.get("type") == "holding"
        pct = s.get("pct", 0)
        chg = s.get("change", 0)
        vr = s.get("vol_ratio", 0)
        price = s.get("price", 0)

        # 持仓/自选标记
        tag = Text("持", style="bold yellow") if is_hold else Text("选", style="dim")

        # 涨跌着色
        if pct > 0:
            price_style, pct_style = "red", "bold red"
        elif pct < 0:
            price_style, pct_style = "green", "bold green"
        else:
            price_style, pct_style = "", ""

        # 量比着色
        if vr >= 1.5:
            vr_text = Text(f"{vr:.2f}", style="bold red")
        elif vr <= 0.5:
            vr_text = Text(f"{vr:.2f}", style="dim")
        else:
            vr_text = Text(f"{vr:.2f}")

        # 换手率着色
        tr = s.get("turnover", 0)
        tr_text = Text(f"{tr:.2f}", style="bold red" if tr >= 5 else "")

        sign = "+" if pct >= 0 else ""
        csign = "+" if chg >= 0 else ""
        amt_wan = s.get("amount", 0) / 10000

        # 盈亏计算（持仓有成本价时）
        pnl_text = None
        if has_holdings:
            cost = entry.get("cost")
            if is_hold and cost and cost > 0 and price > 0:
                pnl_pct = (price - cost) / cost * 100
                pnl_sign = "+" if pnl_pct >= 0 else ""
                pnl_style = "bold red" if pnl_pct > 0 else "bold green" if pnl_pct < 0 else ""
                pnl_text = Text(f"{pnl_sign}{pnl_pct:.1f}%", style=pnl_style)
            else:
                pnl_text = Text("-", style="dim")

        row = [
            tag,
            code,
            s.get("name", ""),
            Text(f"{price:.2f}", style=price_style),
            Text(f"{sign}{pct:.2f}%", style=pct_style),
            Text(f"{csign}{chg:.2f}", style=price_style),
        ]
        if has_holdings:
            row.append(pnl_text)
        row.extend([
            vr_text,
            tr_text,
            f"{s.get('amplitude', 0):.2f}",
            f"{s.get('volume', 0):,}",
            f"{amt_wan:,.0f}",
            f"{s.get('high', 0):.2f}",
            f"{s.get('low', 0):.2f}",
        ])
        table.add_row(*row)

    # 汇总行
    total_amt = sum(s.get("amount", 0) for s in stocks) / 1e8
    avg_pct = sum(s.get("pct", 0) for s in stocks) / len(stocks) if stocks else 0
    avg_vr = sum(s.get("vol_ratio", 0) for s in stocks) / len(stocks) if stocks else 0
    up = sum(1 for s in stocks if s.get("pct", 0) > 0)
    down = sum(1 for s in stocks if s.get("pct", 0) < 0)
    flat = len(stocks) - up - down

    console.print(table)
    console.print(
        f"  合计成交 [bold]{total_amt:.2f}亿[/] | "
        f"均涨幅 [bold]{avg_pct:+.2f}%[/] | "
        f"均量比 [bold]{avg_vr:.2f}[/] | "
        f"涨/跌/平 [red]{up}[/]/[green]{down}[/]/{flat}"
    )


def realtime_loop(config: dict, interval: int = 60):
    """实时行情看板主循环，默认每 60 秒刷新"""
    import os

    symbols = list(config["watchlist"].keys())
    price_engine = AlertEngine(config)

    if not symbols:
        print("watchlist 为空，请先添加股票")
        return

    print(f"启动实时行情看板 | {len(symbols)} 只标的 | 刷新间隔 {interval}s")
    print(f"数据源: 东方财富 push API（单次批量请求，无限流风险）")
    print()

    # 初始化技术信号引擎（加载历史K线）
    signal_engine = TechnicalSignalEngine(symbols, config["watchlist"])
    signal_engine.load_history()

    print(f"按 Ctrl+C 退出\n")

    tick = 0
    try:
        while True:
            tick += 1

            stocks = fetch_realtime_eastmoney(symbols)
            if not stocks:
                logger.warning("未获取到行情数据，等待下次轮询")
                time.sleep(interval)
                continue

            # 清屏并渲染表格
            os.system("clear")
            _render_realtime_board(stocks, tick, config["watchlist"])

            # 价格阈值告警
            sina_compat = {}
            for s in stocks:
                sina_compat[s["code"]] = {
                    "name": s["name"],
                    "price": s["price"],
                    "change_pct": s["pct"],
                }
            price_alerts = price_engine.check(sina_compat)

            # 技术信号告警
            signal_alerts = signal_engine.update_and_check(stocks)

            all_alerts = price_alerts + signal_alerts
            if all_alerts:
                print()
            for alert in all_alerts:
                notify(alert["title"], alert["message"])

            # 非交易时段提示
            if not is_trading_hours():
                print(f"\n  [非交易时段] 数据为最近收盘快照，开盘后自动切换实时")

            print(f"\n  下次刷新: {interval}s 后...")
            time.sleep(interval)

    except KeyboardInterrupt:
        print("\n\n看板已停止")


# ══════════════════════════════════════════
# 2d. 东方财富日K线 + 技术信号引擎
# ══════════════════════════════════════════

EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"


TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def fetch_daily_klines(symbol: str, days: int = 60) -> list[float]:
    """获取近 N 日收盘价序列

    A股: 优先新浪、备选东方财富
    港股: 腾讯财经接口
    """
    if is_hk_symbol(symbol):
        return _fetch_klines_hk(symbol, days)
    return _fetch_klines_a(symbol, days)


def _fetch_klines_hk(symbol: str, days: int) -> list[float]:
    """港股K线 - 腾讯财经接口"""
    code = hk_code(symbol).lower()  # hk00700
    try:
        url = f"{TENCENT_KLINE_URL}?param=hk{code},day,,,{days},qfq"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        stock_data = data.get("data", {}).get(f"hk{code}", {})
        klines = stock_data.get("qfqday") or stock_data.get("day", [])
        return [float(k[2]) for k in klines]  # k[2] = close
    except Exception as e:
        logger.error(f"获取 {symbol} 港股K线失败: {e}")
        return []


def _fetch_klines_a(symbol: str, days: int) -> list[float]:
    """A股K线 - 优先新浪、备选东方财富"""
    # 方式1: 新浪K线
    try:
        prefix = get_stock_prefix(symbol)
        params = {
            "symbol": f"{prefix}{symbol}",
            "scale": "240", "ma": "no",
            "datalen": str(days),
        }
        resp = requests.get(
            SINA_KLINE_URL, params=params,
            headers=SINA_HEADERS, timeout=10,
        )
        import json as _json
        data = _json.loads(resp.text)
        if data and len(data) > 0:
            return [float(d["close"]) for d in data]
    except Exception as e:
        logger.warning(f"{symbol} 新浪K线失败: {e}，尝试东方财富")

    # 方式2: 东方财富K线
    secid = _em_secid(symbol)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": "101", "fqt": "1",
        "end": "20500101", "lmt": str(days),
        "ut": EM_UT,
    }
    for attempt in range(2):
        try:
            resp = requests.get(EASTMONEY_KLINE_URL, params=params, timeout=10)
            data = resp.json()
            klines = data.get("data", {}).get("klines", [])
            return [float(k.split(",")[2]) for k in klines]
        except Exception:
            time.sleep(1)

    logger.error(f"获取 {symbol} 日K线全部失败")
    return []


def _ema_series(data: list[float], span: int) -> list[float]:
    """计算 EMA 序列"""
    if not data:
        return []
    mult = 2.0 / (span + 1)
    vals = [data[0]]
    for d in data[1:]:
        vals.append(d * mult + vals[-1] * (1 - mult))
    return vals


class TechnicalSignalEngine:
    """技术信号检测引擎

    启动时加载历史日K线，盘中每 tick 用实时价格替换最后一根K线，
    检测均线金叉/死叉、MACD翻红翻绿、RSI超买超卖、放量突破。
    """

    COOLDOWN_SEC = 600  # 同一信号 10 分钟冷却

    def __init__(self, symbols: list[str], watchlist: dict):
        self.watchlist = watchlist
        self.history: dict[str, list[float]] = {}   # {code: [close1, close2, ...]}
        self.prev_state: dict[str, dict] = {}
        self.cooldowns: dict[str, float] = {}

    def load_history(self):
        """启动时批量加载历史K线（约 0.3s × 股票数）"""
        from rich.console import Console
        console = Console()
        total = len(self.watchlist)
        for i, (sym, info) in enumerate(self.watchlist.items(), 1):
            name = info.get("name", sym)
            console.print(f"  [{i}/{total}] 加载 {name}({sym}) K线...", end=" ")
            closes = fetch_daily_klines(sym, days=60)
            if len(closes) >= 20:
                self.history[sym] = closes
                self.prev_state[sym] = self._calc_state(closes)
                console.print(f"[green]{len(closes)}条[/]")
            else:
                console.print(f"[yellow]数据不足({len(closes)}条)，跳过[/]")
            time.sleep(0.3)
        console.print(f"  技术信号引擎就绪，已加载 {len(self.history)}/{total} 只\n")

    # ── 指标计算 ──

    @staticmethod
    def _calc_ma(prices: list[float], period: int) -> float | None:
        if len(prices) < period:
            return None
        return sum(prices[-period:]) / period

    @staticmethod
    def _calc_rsi(prices: list[float], period: int = 14) -> float | None:
        if len(prices) < period + 1:
            return None
        gains, losses = [], []
        for i in range(-period, 0):
            diff = prices[i] - prices[i - 1]
            gains.append(max(0, diff))
            losses.append(max(0, -diff))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    @staticmethod
    def _calc_macd_hist(prices: list[float]) -> float | None:
        if len(prices) < 35:
            return None
        ema12 = _ema_series(prices, 12)
        ema26 = _ema_series(prices, 26)
        dif = [a - b for a, b in zip(ema12, ema26)]
        dea = _ema_series(dif, 9)
        return (dif[-1] - dea[-1]) * 2

    def _calc_state(self, prices: list[float]) -> dict:
        ma5 = self._calc_ma(prices, 5)
        ma10 = self._calc_ma(prices, 10)
        rsi = self._calc_rsi(prices, 14)
        macd_h = self._calc_macd_hist(prices)
        return {
            "ma5_above_ma10": (ma5 > ma10) if ma5 and ma10 else None,
            "rsi": rsi,
            "rsi_ob": (rsi > 70) if rsi is not None else None,
            "rsi_os": (rsi < 30) if rsi is not None else None,
            "macd_pos": (macd_h > 0) if macd_h is not None else None,
        }

    # ── 冷却控制 ──

    def _cooled(self, key: str) -> bool:
        return (time.time() - self.cooldowns.get(key, 0)) >= self.COOLDOWN_SEC

    def _fire(self, key: str):
        self.cooldowns[key] = time.time()

    # ── 信号检测主入口 ──

    def update_and_check(self, realtime: list[dict]) -> list[dict]:
        """用实时价格替换最后一根K线，检测信号翻转"""
        alerts = []

        for stock in realtime:
            code = stock.get("code", "")
            if code not in self.history:
                continue

            name = stock.get("name", code)
            price = stock.get("price", 0)
            vol_ratio = stock.get("vol_ratio", 0)
            pct = stock.get("pct", 0)
            if price <= 0:
                continue

            # 用实时价替换最后一根收盘价
            prices = self.history[code][:-1] + [price]
            new = self._calc_state(prices)
            old = self.prev_state.get(code, {})

            # 1) 均线金叉
            if old.get("ma5_above_ma10") is False and new.get("ma5_above_ma10") is True:
                k = f"{code}_golden"
                if self._cooled(k):
                    self._fire(k)
                    alerts.append({"title": f"📊 {name} 均线金叉",
                                   "message": f"{name}({code}) MA5上穿MA10，现价 {price:.2f}"})

            # 2) 均线死叉
            if old.get("ma5_above_ma10") is True and new.get("ma5_above_ma10") is False:
                k = f"{code}_death"
                if self._cooled(k):
                    self._fire(k)
                    alerts.append({"title": f"📊 {name} 均线死叉",
                                   "message": f"{name}({code}) MA5下穿MA10，现价 {price:.2f}"})

            # 3) MACD 翻红
            if old.get("macd_pos") is False and new.get("macd_pos") is True:
                k = f"{code}_macd_bull"
                if self._cooled(k):
                    self._fire(k)
                    alerts.append({"title": f"📊 {name} MACD翻红",
                                   "message": f"{name}({code}) MACD柱转正，现价 {price:.2f}"})

            # 4) MACD 翻绿
            if old.get("macd_pos") is True and new.get("macd_pos") is False:
                k = f"{code}_macd_bear"
                if self._cooled(k):
                    self._fire(k)
                    alerts.append({"title": f"📊 {name} MACD翻绿",
                                   "message": f"{name}({code}) MACD柱转负，现价 {price:.2f}"})

            # 5) RSI 超买
            if old.get("rsi_ob") is False and new.get("rsi_ob") is True:
                k = f"{code}_rsi_ob"
                if self._cooled(k):
                    self._fire(k)
                    rsi = new.get("rsi", 0)
                    alerts.append({"title": f"⚠️ {name} RSI超买",
                                   "message": f"{name}({code}) RSI={rsi:.0f}>70，现价 {price:.2f}"})

            # 6) RSI 超卖
            if old.get("rsi_os") is False and new.get("rsi_os") is True:
                k = f"{code}_rsi_os"
                if self._cooled(k):
                    self._fire(k)
                    rsi = new.get("rsi", 0)
                    alerts.append({"title": f"💡 {name} RSI超卖",
                                   "message": f"{name}({code}) RSI={rsi:.0f}<30，现价 {price:.2f}"})

            # 7) 放量突破
            if vol_ratio >= 2.0 and pct >= 2.0:
                k = f"{code}_vol_break"
                if self._cooled(k):
                    self._fire(k)
                    alerts.append({"title": f"🔥 {name} 放量突破",
                                   "message": f"{name}({code}) 量比{vol_ratio:.1f} 涨{pct:+.1f}%，现价 {price:.2f}"})

            self.prev_state[code] = new

        return alerts


# ══════════════════════════════════════════
# 3. macOS 通知
# ══════════════════════════════════════════

WEB_DASHBOARD_URL = "http://localhost:3120/alerts"


def notify(title: str, message: str, sound: str = "default", group: str = ""):
    """发送 macOS 通知，按优先级尝试多种方式

    Args:
        group: terminal-notifier 分组 ID，同 group 的通知会互相覆盖。
               留空则用时间戳生成唯一 ID，确保每条通知独立显示。
    """
    # 转义双引号
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')

    # 每条通知独立 group，避免互相覆盖
    if not group:
        group = f"stock_{int(time.time() * 1000)}"

    # 方式1: terminal-notifier（点击跳转 web dashboard）
    try:
        result = subprocess.run(
            ["which", "terminal-notifier"], capture_output=True, timeout=3,
        )
        if result.returncode == 0:
            subprocess.run(
                ["terminal-notifier", "-title", title, "-message", message,
                 "-sound", sound, "-group", group,
                 "-open", WEB_DASHBOARD_URL],
                capture_output=True, timeout=5,
            )
            logger.info(f"通知已发送(terminal-notifier): [{title}] {message}")
            return
    except Exception:
        pass

    # 方式2: osascript display notification
    try:
        script = f'display notification "{safe_msg}" with title "{safe_title}" sound name "{sound}"'
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, timeout=5,
        )
        if result.returncode == 0:
            logger.info(f"通知已发送(osascript): [{title}] {message}")
            return
    except Exception:
        pass

    # 方式3: osascript 弹窗（始终可见，不依赖通知权限）
    try:
        script = f'display dialog "{safe_msg}" with title "{safe_title}" buttons {{"OK"}} giving up after 5'
        subprocess.run(
            ["osascript", "-e", script], capture_output=True, timeout=8,
        )
        logger.info(f"通知已发送(dialog): [{title}] {message}")
    except Exception as e:
        logger.error(f"发送通知失败: {e}")


# ══════════════════════════════════════════
# 4. 告警引擎
# ══════════════════════════════════════════

class AlertEngine:
    """告警检测引擎，支持价格阈值告警和大涨大跌自动告警"""

    def __init__(self, config: dict):
        self.config = config
        self.cooldowns: dict[str, float] = {}  # {alert_key: last_trigger_timestamp}

    def _is_cooled_down(self, key: str) -> bool:
        """检查告警是否在冷却期内"""
        cooldown_sec = self.config["settings"]["cooldown_minutes"] * 60
        last = self.cooldowns.get(key, 0)
        return (time.time() - last) >= cooldown_sec

    def _trigger(self, key: str):
        """记录告警触发时间"""
        self.cooldowns[key] = time.time()

    def check(self, quotes: dict) -> list[dict]:
        """检查所有告警条件

        Returns:
            list of {symbol, name, title, message}
        """
        alerts = []
        big_move_pct = self.config["settings"]["big_move_pct"]
        alert_rules = load_alerts()

        for symbol, quote in quotes.items():
            name = quote["name"]
            price = quote["price"]
            change_pct = quote["change_pct"]

            if price <= 0:
                continue

            alert_entry = alert_rules.get(symbol, {})

            # ── 用户自定义价格上限告警 ──
            above = alert_entry.get("above")
            if above is not None and price >= above:
                key = f"{symbol}_above_{above}"
                if self._is_cooled_down(key):
                    self._trigger(key)
                    alerts.append({
                        "symbol": symbol,
                        "name": name,
                        "title": f"📈 {name} 突破上限",
                        "message": f"{name}({symbol}) 当前 {price:.2f}，已突破设定上限 {above}",
                    })

            # ── 用户自定义价格下限告警 ──
            below = alert_entry.get("below")
            if below is not None and price <= below:
                key = f"{symbol}_below_{below}"
                if self._is_cooled_down(key):
                    self._trigger(key)
                    alerts.append({
                        "symbol": symbol,
                        "name": name,
                        "title": f"📉 {name} 跌破下限",
                        "message": f"{name}({symbol}) 当前 {price:.2f}，已跌破设定下限 {below}",
                    })

            # ── 大涨自动告警 ──
            if change_pct >= big_move_pct:
                key = f"{symbol}_big_up"
                if self._is_cooled_down(key):
                    self._trigger(key)
                    alerts.append({
                        "symbol": symbol,
                        "name": name,
                        "title": f"🔥 {name} 大涨 +{change_pct:.1f}%",
                        "message": f"{name}({symbol}) 当前 {price:.2f}，涨幅 +{change_pct:.1f}%",
                    })

            # ── 大跌自动告警 ──
            if change_pct <= -big_move_pct:
                key = f"{symbol}_big_down"
                if self._is_cooled_down(key):
                    self._trigger(key)
                    alerts.append({
                        "symbol": symbol,
                        "name": name,
                        "title": f"⚠️ {name} 大跌 {change_pct:.1f}%",
                        "message": f"{name}({symbol}) 当前 {price:.2f}，跌幅 {change_pct:.1f}%",
                    })

        return alerts


# ══════════════════════════════════════════
# 5. 交易时段检测
# ══════════════════════════════════════════

def is_trading_hours() -> bool:
    """判断当前是否在 A 股交易时段（周一至周五 9:30-11:30, 13:00-15:00）"""
    now = datetime.now()
    # 周末不交易
    if now.weekday() >= 5:
        return False
    t = now.hour * 100 + now.minute
    return (930 <= t <= 1130) or (1300 <= t <= 1500)


# ══════════════════════════════════════════
# 6. 终端状态显示
# ══════════════════════════════════════════

def print_status_line(quotes: dict):
    """在终端打印一行简要行情状态"""
    now = datetime.now().strftime("%H:%M:%S")
    parts = []
    for symbol, q in quotes.items():
        pct = q["change_pct"]
        sign = "+" if pct >= 0 else ""
        parts.append(f"{symbol} {q['price']:.2f}({sign}{pct:.2f}%)")
    line = " | ".join(parts)
    # 使用 \r 覆盖当前行，\033[K 清除行尾残余字符
    print(f"\r\033[K[{now}] {line}", end="", flush=True)


# ══════════════════════════════════════════
# 7. 监控主循环
# ══════════════════════════════════════════

def monitor_loop(config: dict):
    """主监控循环"""
    interval = config["settings"]["poll_interval"]
    symbols = list(config["watchlist"].keys())
    engine = AlertEngine(config)

    if not symbols:
        print("❌ watchlist 为空，请先添加股票")
        return

    stock_names = ", ".join(
        f'{s}({config["watchlist"][s]["name"]})' for s in symbols
    )
    print(f"🔍 开始监控 {len(symbols)} 只股票: {stock_names}")
    print(f"⚙️  轮询间隔: {interval}s | 大涨大跌阈值: ±{config['settings']['big_move_pct']}%"
          f" | 冷却: {config['settings']['cooldown_minutes']}min")
    print(f"📋 交易时段: 9:30-11:30, 13:00-15:00 (非交易时段自动休眠)")
    print(f"按 Ctrl+C 退出\n")

    try:
        while True:
            if not is_trading_hours():
                now = datetime.now().strftime("%H:%M:%S")
                print(f"\r\033[K[{now}] ⏸  非交易时段，等待中...", end="", flush=True)
                time.sleep(60)
                continue

            quotes = fetch_realtime_sina(symbols)
            if not quotes:
                logger.warning("未获取到行情数据，等待下次轮询")
                time.sleep(interval)
                continue

            # 检查告警
            alerts = engine.check(quotes)
            if alerts:
                # 先换行，避免通知消息和状态行混在一起
                print()
            for alert in alerts:
                notify(alert["title"], alert["message"])

            # 终端状态
            print_status_line(quotes)

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\n👋 监控已停止")


# ══════════════════════════════════════════
# 8. CLI
# ══════════════════════════════════════════

def cmd_add(args, config: dict):
    """添加或更新股票提醒"""
    symbol = args.add.upper() if args.add.upper().startswith("HK") else args.add
    above = args.above
    below = args.below

    # 如果 watchlist 中没有此股票，先获取名称
    if symbol not in config["watchlist"]:
        quotes = fetch_realtime_sina([symbol])
        name = quotes.get(symbol, {}).get("name", symbol)
        config["watchlist"][symbol] = {
            "name": name,
            "type": "holding" if args.holding else "watching",
            "cost": None, "shares": None,
        }

    if args.holding:
        config["watchlist"][symbol]["type"] = "holding"
    if args.cost is not None:
        config["watchlist"][symbol]["cost"] = args.cost
    if args.shares is not None:
        config["watchlist"][symbol]["shares"] = args.shares

    save_config(config)

    # 告警写到 alert_config.json
    if above is not None or below is not None:
        alert_rules = load_alerts()
        if symbol not in alert_rules:
            alert_rules[symbol] = {}
        if above is not None:
            alert_rules[symbol]["above"] = above
        if below is not None:
            alert_rules[symbol]["below"] = below
        save_alerts(alert_rules)

    entry = config["watchlist"][symbol]
    alert_entry = load_alerts().get(symbol, {})
    tag = "持仓" if entry.get("type") == "holding" else "自选"
    print(f"✅ 已设置 [{tag}] {entry['name']}({symbol}):")
    if entry.get("type") == "holding":
        cost_str = f"{entry['cost']:.2f}" if entry.get("cost") else "未设置"
        shares_str = f"{entry['shares']}" if entry.get("shares") else "未设置"
        print(f"   成本价: {cost_str}  |  持仓量: {shares_str}")
    print(f"   上限告警: {alert_entry.get('above', '未设置')}")
    print(f"   下限告警: {alert_entry.get('below', '未设置')}")


def cmd_remove(args, config: dict):
    """删除股票提醒"""
    symbol = args.remove
    if symbol in config["watchlist"]:
        name = config["watchlist"][symbol]["name"]
        del config["watchlist"][symbol]
        save_config(config)
        # 同步清理告警
        alert_rules = load_alerts()
        if symbol in alert_rules:
            del alert_rules[symbol]
            save_alerts(alert_rules)
        print(f"✅ 已删除 {name}({symbol})")
    else:
        print(f"❌ {symbol} 不在 watchlist 中")


def cmd_list(config: dict):
    """显示当前所有提醒设置，分持仓/自选两组"""
    watchlist = config["watchlist"]
    settings = config["settings"]
    alert_rules = load_alerts()

    print(f"\n📋 全局设置:")
    print(f"   轮询间隔: {settings['poll_interval']}s")
    print(f"   大涨大跌阈值: ±{settings['big_move_pct']}%")
    print(f"   冷却时间: {settings['cooldown_minutes']}min\n")

    if not watchlist:
        print("   (列表为空)")
        return

    holdings = {s: e for s, e in watchlist.items() if e.get("type") == "holding"}
    watching = {s: e for s, e in watchlist.items() if e.get("type") != "holding"}

    if holdings:
        print(f"💰 持仓列表 ({len(holdings)} 只):")
        print(f"   {'代码':<10} {'名称':<12} {'成本':>8} {'持仓量':>8} {'上限':>8} {'下限':>8}")
        print(f"   {'─'*10} {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*8}")
        for symbol, entry in holdings.items():
            name = entry["name"]
            ae = alert_rules.get(symbol, {})
            cost_str = f"{entry['cost']:.2f}" if entry.get("cost") else "-"
            shares_str = f"{entry['shares']}" if entry.get("shares") else "-"
            above_str = f"{ae['above']}" if ae.get("above") is not None else "-"
            below_str = f"{ae['below']}" if ae.get("below") is not None else "-"
            print(f"   {symbol:<10} {name:<12} {cost_str:>8} {shares_str:>8} {above_str:>8} {below_str:>8}")
        print()

    if watching:
        print(f"👀 自选列表 ({len(watching)} 只):")
        print(f"   {'代码':<10} {'名称':<12} {'上限':>8} {'下限':>8}")
        print(f"   {'─'*10} {'─'*12} {'─'*8} {'─'*8}")
        for symbol, entry in watching.items():
            name = entry["name"]
            ae = alert_rules.get(symbol, {})
            above_str = f"{ae['above']}" if ae.get("above") is not None else "-"
            below_str = f"{ae['below']}" if ae.get("below") is not None else "-"
            print(f"   {symbol:<10} {name:<12} {above_str:>8} {below_str:>8}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="macOS 股票盯盘提醒系统 - 基于新浪财经实时行情",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                 # 启动监控
  %(prog)s --add 688676 --above 100 --below 85  # 添加价格提醒
  %(prog)s --list                          # 查看所有提醒
  %(prog)s --remove 688676                 # 删除提醒
  %(prog)s --interval 15 --threshold 3.0   # 自定义参数启动
        """,
    )

    parser.add_argument("--add", metavar="SYMBOL", help="添加/更新股票（默认自选，配合 --holding 设为持仓）")
    parser.add_argument("--holding", action="store_true", help="标记为持仓 (配合 --add)")
    parser.add_argument("--cost", type=float, help="持仓成本价 (配合 --add --holding)")
    parser.add_argument("--shares", type=int, help="持仓数量 (配合 --add --holding)")
    parser.add_argument("--above", type=float, help="价格上限告警 (配合 --add)")
    parser.add_argument("--below", type=float, help="价格下限告警 (配合 --add)")
    parser.add_argument("--remove", metavar="SYMBOL", help="删除股票提醒")
    parser.add_argument("--list", action="store_true", help="查看当前所有提醒设置")
    parser.add_argument("--realtime", action="store_true",
                        help="实时行情看板（含量比/换手率，默认60s刷新）")
    parser.add_argument("--interval", type=int, help="轮询间隔（秒）")
    parser.add_argument("--threshold", type=float, help="大涨大跌阈值（%%）")

    args = parser.parse_args()
    config = load_config()

    # ── 命令分发 ──
    if args.add:
        cmd_add(args, config)
        return

    if args.remove:
        cmd_remove(args, config)
        return

    if args.list:
        cmd_list(config)
        return

    # ── 应用命令行参数覆盖 ──
    if args.interval:
        config["settings"]["poll_interval"] = args.interval
    if args.threshold:
        config["settings"]["big_move_pct"] = args.threshold

    # ── 实时行情看板 ──
    if args.realtime:
        interval = config["settings"]["poll_interval"] if args.interval else 60
        realtime_loop(config, interval=interval)
        return

    # ── 默认：告警监控模式 ──
    monitor_loop(config)


if __name__ == "__main__":
    main()
