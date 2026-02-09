"""
AIDC电力基建股票数据批量采集脚本

带完善限流控制的批量数据采集，支持实时行情、历史K线、财务数据、个股新闻。
终端使用 rich 渲染彩色表格，同时输出 JSON 文件。

Usage:
    poetry run python src/tools/stock_data_fetcher.py
    poetry run python src/tools/stock_data_fetcher.py --symbols 688676,002335,600089
    poetry run python src/tools/stock_data_fetcher.py --interval 2.0
    poetry run python src/tools/stock_data_fetcher.py --quick
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import akshare as ak
import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

# ──────────────────────────── 配置与常量 ────────────────────────────

AIDC_WATCHLIST = {
    "688676": "金盘科技",
    "002335": "科华数据",
    "600089": "特变电工",
    "601179": "中国西电",
    "600517": "国网英大",
    "300376": "易事特",
    "002121": "科陆电子",
    "603063": "禾望电气",
    "301388": "欣灵电气",
    "600268": "国电南自",
    "600312": "平高电气",
}

console = Console()

# ──────────────────────────── 限流控制器 ────────────────────────────


class RateLimiter:
    """令牌桶 + 自适应退避限流器"""

    def __init__(self, min_interval: float = 1.5):
        self.base_interval = min_interval
        self.current_interval = min_interval
        self.last_request_time = 0.0
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.total_failures = 0
        self._circuit_open = False

    def wait(self):
        """请求前调用，按间隔等待"""
        # 全局熔断检查
        if self._circuit_open:
            console.print("[bold red]熔断器触发，暂停 60 秒...[/]")
            time.sleep(60)
            self._circuit_open = False
            self.consecutive_failures = 0
            self.current_interval = self.base_interval

        elapsed = time.time() - self.last_request_time
        if elapsed < self.current_interval:
            time.sleep(self.current_interval - elapsed)
        self.last_request_time = time.time()

    def report_success(self):
        """成功后调用，连续 3 次成功回退间隔"""
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        if self.consecutive_successes >= 3 and self.current_interval > self.base_interval:
            self.current_interval = max(
                self.base_interval, self.current_interval / 2
            )
            self.consecutive_successes = 0

    def report_failure(self):
        """失败后调用，指数退避"""
        self.consecutive_successes = 0
        self.consecutive_failures += 1
        self.total_failures += 1
        self.current_interval = min(
            60, self.current_interval * 2
        )
        # 连续 10 次失败触发熔断
        if self.consecutive_failures >= 10:
            self._circuit_open = True


# ──────────────────────────── 带重试的请求封装 ────────────────────────────


def _call_with_retry(fn, limiter: RateLimiter, max_retries: int = 5, label: str = ""):
    """封装 akshare 调用，含限流等待 + 指数退避重试"""
    for attempt in range(1, max_retries + 1):
        limiter.wait()
        try:
            result = fn()
            limiter.report_success()
            return result
        except Exception as e:
            limiter.report_failure()
            if attempt < max_retries:
                wait = min(60, 2 ** attempt)
                console.print(
                    f"  [yellow]⚠ {label} 第{attempt}次失败: {e}，{wait}s 后重试[/]"
                )
                time.sleep(wait)
            else:
                console.print(f"  [red]✗ {label} 全部{max_retries}次重试失败: {e}[/]")
                return None


# ──────────────────────────── 数据采集函数 ────────────────────────────


def fetch_realtime(symbol: str, name: str, limiter: RateLimiter) -> dict:
    """获取实时行情"""
    console.print(f"  [cyan]获取实时行情 {symbol} {name}...[/]")

    # 优先使用单股接口
    result = _call_with_retry(
        lambda: ak.stock_individual_info_em(symbol=symbol),
        limiter,
        label=f"{symbol}实时行情(individual)",
    )
    if result is not None and not result.empty:
        info = dict(zip(result["item"], result["value"]))
        data = {
            "symbol": symbol,
            "name": name,
            "latest_price": _safe_float(info.get("最新")),
            "change_pct": _safe_float(info.get("涨跌幅")),
            "total_market_cap": _safe_float(info.get("总市值")),
            "float_market_cap": _safe_float(info.get("流通市值")),
            "pe_ratio": _safe_float(info.get("市盈率(动)")),
            "pb_ratio": _safe_float(info.get("市净率")),
            "volume": _safe_float(info.get("成交量")),
            "turnover": _safe_float(info.get("换手率")),
            "52w_high": _safe_float(info.get("52周最高")),
            "52w_low": _safe_float(info.get("52周最低")),
        }
        console.print(f"  [green]✓ {symbol} 实时行情获取成功[/]")
        return data

    # fallback: 全市场快照筛选
    console.print(f"  [yellow]单股接口失败，尝试全市场快照...[/]")
    df = _call_with_retry(
        lambda: ak.stock_zh_a_spot_em(),
        limiter,
        label=f"{symbol}实时行情(spot)",
    )
    if df is not None and not df.empty:
        row = df[df["代码"] == symbol]
        if not row.empty:
            r = row.iloc[0]
            data = {
                "symbol": symbol,
                "name": name,
                "latest_price": _safe_float(r.get("最新价")),
                "change_pct": _safe_float(r.get("涨跌幅")),
                "total_market_cap": _safe_float(r.get("总市值")),
                "float_market_cap": _safe_float(r.get("流通市值")),
                "pe_ratio": _safe_float(r.get("市盈率-动态")),
                "pb_ratio": _safe_float(r.get("市净率")),
                "volume": _safe_float(r.get("成交量")),
                "turnover": _safe_float(r.get("换手率")),
                "52w_high": _safe_float(r.get("52周最高")),
                "52w_low": _safe_float(r.get("52周最低")),
            }
            console.print(f"  [green]✓ {symbol} 实时行情获取成功 (fallback)[/]")
            return data

    console.print(f"  [red]✗ {symbol} 实时行情获取失败[/]")
    return {"symbol": symbol, "name": name}


def fetch_hist_kline(
    symbol: str, limiter: RateLimiter, days: int = 60
) -> dict:
    """获取历史K线 + 计算技术指标"""
    console.print(f"  [cyan]获取历史K线 {symbol} (近{days}日)...[/]")

    # 多取一些天用于指标计算窗口
    fetch_days = days + 60
    end_date = datetime.now() - timedelta(days=1)
    start_date = end_date - timedelta(days=int(fetch_days * 1.6))

    df = _call_with_retry(
        lambda: ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="qfq",
        ),
        limiter,
        label=f"{symbol}历史K线",
    )

    if df is None or df.empty:
        console.print(f"  [red]✗ {symbol} 历史K线获取失败[/]")
        return {}

    # 重命名
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "最高": "high",
        "最低": "low", "收盘": "close", "成交量": "volume",
        "涨跌幅": "pct_change", "换手率": "turnover",
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    close = df["close"]

    # 技术指标计算
    df["sma5"] = close.rolling(5).mean()
    df["sma10"] = close.rolling(10).mean()
    df["sma20"] = close.rolling(20).mean()
    df["sma60"] = close.rolling(60).mean()
    df["ema12"] = close.ewm(span=12, adjust=False).mean()
    df["ema26"] = close.ewm(span=26, adjust=False).mean()
    df["rsi14"] = _calc_rsi(close, 14)
    macd_data = _calc_macd(close)
    df["macd_dif"] = macd_data["dif"]
    df["macd_dea"] = macd_data["dea"]
    df["macd_hist"] = macd_data["hist"]

    # 只保留最近 days 条记录
    df = df.tail(days).reset_index(drop=True)

    latest = df.iloc[-1]
    result = {
        "period_days": days,
        "latest_close": float(latest["close"]),
        "latest_date": latest["date"].strftime("%Y-%m-%d"),
        "sma5": _safe_float(latest.get("sma5")),
        "sma10": _safe_float(latest.get("sma10")),
        "sma20": _safe_float(latest.get("sma20")),
        "sma60": _safe_float(latest.get("sma60")),
        "rsi14": _safe_float(latest.get("rsi14")),
        "macd_dif": _safe_float(latest.get("macd_dif")),
        "macd_dea": _safe_float(latest.get("macd_dea")),
        "macd_hist": _safe_float(latest.get("macd_hist")),
        "period_high": float(df["high"].max()),
        "period_low": float(df["low"].min()),
        "period_return_pct": round(
            (float(df.iloc[-1]["close"]) / float(df.iloc[0]["close"]) - 1) * 100, 2
        ),
        "avg_turnover": round(float(df["turnover"].mean()), 2),
    }
    console.print(f"  [green]✓ {symbol} 历史K线 + 技术指标计算完成[/]")
    return result


def fetch_financials(symbol: str, limiter: RateLimiter) -> dict:
    """获取财务指标"""
    console.print(f"  [cyan]获取财务指标 {symbol}...[/]")
    year = str(datetime.now().year - 1)

    df = _call_with_retry(
        lambda: ak.stock_financial_analysis_indicator(symbol=symbol, start_year=year),
        limiter,
        label=f"{symbol}财务指标",
    )

    if df is None or df.empty:
        console.print(f"  [red]✗ {symbol} 财务指标获取失败[/]")
        return {}

    df["日期"] = pd.to_datetime(df["日期"])
    df = df.sort_values("日期", ascending=False)
    latest = df.iloc[0]

    result = {
        "report_date": latest["日期"].strftime("%Y-%m-%d"),
        "roe_pct": _safe_float(latest.get("净资产收益率(%)")),
        "net_margin_pct": _safe_float(latest.get("销售净利率(%)")),
        "operating_margin_pct": _safe_float(latest.get("营业利润率(%)")),
        "revenue_growth_pct": _safe_float(latest.get("主营业务收入增长率(%)")),
        "net_profit_growth_pct": _safe_float(latest.get("净利润增长率(%)")),
        "current_ratio": _safe_float(latest.get("流动比率")),
        "debt_to_asset_pct": _safe_float(latest.get("资产负债率(%)")),
        "eps": _safe_float(latest.get("加权每股收益(元)")),
        "fcf_per_share": _safe_float(latest.get("每股经营性现金流(元)")),
    }
    console.print(f"  [green]✓ {symbol} 财务指标获取成功[/]")
    return result


def fetch_news(symbol: str, limiter: RateLimiter, max_news: int = 5) -> list:
    """获取个股新闻"""
    console.print(f"  [cyan]获取新闻 {symbol}...[/]")

    df = _call_with_retry(
        lambda: ak.stock_news_em(symbol=symbol),
        limiter,
        label=f"{symbol}新闻",
    )

    if df is None or df.empty:
        console.print(f"  [yellow]⚠ {symbol} 新闻获取失败，跳过[/]")
        return []

    news_list = []
    for _, row in df.head(max_news).iterrows():
        news_list.append({
            "title": str(row.get("新闻标题", "")).strip(),
            "time": str(row.get("发布时间", "")),
            "source": str(row.get("文章来源", "")).strip(),
        })
    console.print(f"  [green]✓ {symbol} 获取 {len(news_list)} 条新闻[/]")
    return news_list


# ──────────────────────────── 技术指标计算 ────────────────────────────


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> dict:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return {"dif": dif, "dea": dea, "hist": hist}


def _safe_float(val, default=None):
    """安全转换为 float"""
    if val is None:
        return default
    try:
        v = float(val)
        if np.isnan(v) or np.isinf(v):
            return default
        return round(v, 4)
    except (ValueError, TypeError):
        return default


# ──────────────────────────── 终端渲染 ────────────────────────────


def render_realtime_table(all_data: list[dict]):
    """渲染实时行情表格"""
    table = Table(title="AIDC 电力基建标的 - 实时行情", show_lines=True)
    table.add_column("代码", style="bold")
    table.add_column("名称", style="bold")
    table.add_column("最新价", justify="right")
    table.add_column("涨跌幅%", justify="right")
    table.add_column("总市值(亿)", justify="right")
    table.add_column("PE(动)", justify="right")
    table.add_column("PB", justify="right")
    table.add_column("换手率%", justify="right")

    for d in all_data:
        if not d.get("latest_price"):
            table.add_row(d.get("symbol", ""), d.get("name", ""), *["N/A"] * 6)
            continue

        chg = d.get("change_pct")
        chg_style = "red" if chg and chg > 0 else "green" if chg and chg < 0 else ""
        chg_str = f"{chg:+.2f}" if chg is not None else "N/A"

        mcap = d.get("total_market_cap")
        mcap_str = f"{mcap / 1e8:.1f}" if mcap else "N/A"

        table.add_row(
            d["symbol"],
            d["name"],
            f"{d['latest_price']:.2f}",
            Text(chg_str, style=chg_style),
            mcap_str,
            f"{d['pe_ratio']:.1f}" if d.get("pe_ratio") else "N/A",
            f"{d['pb_ratio']:.2f}" if d.get("pb_ratio") else "N/A",
            f"{d['turnover']:.2f}" if d.get("turnover") else "N/A",
        )

    console.print(table)


def render_analysis_table(all_data: list[dict]):
    """渲染技术分析 + 性价比评分表格"""
    table = Table(title="AIDC 电力基建标的 - 技术分析 & 性价比评分", show_lines=True)
    table.add_column("代码", style="bold")
    table.add_column("名称", style="bold")
    table.add_column("RSI(14)", justify="right")
    table.add_column("MACD柱", justify="right")
    table.add_column("均线排列", justify="center")
    table.add_column("区间涨幅%", justify="right")
    table.add_column("ROE%", justify="right")
    table.add_column("利润增速%", justify="right")
    table.add_column("性价比", justify="center", style="bold")

    for d in all_data:
        kline = d.get("kline", {})
        fin = d.get("financials", {})

        if not kline:
            table.add_row(d.get("symbol", ""), d.get("name", ""), *["N/A"] * 7)
            continue

        # RSI 着色
        rsi = kline.get("rsi14")
        if rsi is not None:
            if rsi > 70:
                rsi_text = Text(f"{rsi:.1f}", style="bold red")
            elif rsi < 30:
                rsi_text = Text(f"{rsi:.1f}", style="bold green")
            else:
                rsi_text = Text(f"{rsi:.1f}")
        else:
            rsi_text = Text("N/A")

        # MACD 柱
        macd_h = kline.get("macd_hist")
        if macd_h is not None:
            macd_style = "red" if macd_h > 0 else "green"
            macd_text = Text(f"{macd_h:.3f}", style=macd_style)
        else:
            macd_text = Text("N/A")

        # 均线排列判断
        sma5 = kline.get("sma5")
        sma10 = kline.get("sma10")
        sma20 = kline.get("sma20")
        if sma5 and sma10 and sma20:
            if sma5 > sma10 > sma20:
                ma_text = Text("多头排列 ↑", style="bold red")
            elif sma5 < sma10 < sma20:
                ma_text = Text("空头排列 ↓", style="bold green")
            else:
                ma_text = Text("震荡整理 ─")
        else:
            ma_text = Text("N/A")

        # 区间涨幅
        ret = kline.get("period_return_pct")
        if ret is not None:
            ret_style = "red" if ret > 0 else "green"
            ret_text = Text(f"{ret:+.1f}", style=ret_style)
        else:
            ret_text = Text("N/A")

        # 性价比评分 (简单加权评分)
        score = _calc_value_score(d)
        if score is not None:
            if score >= 7:
                score_text = Text(f"★ {score:.1f}", style="bold red")
            elif score >= 5:
                score_text = Text(f"● {score:.1f}", style="yellow")
            else:
                score_text = Text(f"○ {score:.1f}", style="dim")
        else:
            score_text = Text("N/A")

        table.add_row(
            d.get("symbol", ""),
            d.get("name", ""),
            rsi_text,
            macd_text,
            ma_text,
            ret_text,
            f"{fin.get('roe_pct', 'N/A')}" if fin.get("roe_pct") is not None else "N/A",
            f"{fin.get('net_profit_growth_pct', 'N/A')}" if fin.get("net_profit_growth_pct") is not None else "N/A",
            score_text,
        )

    console.print(table)


def _calc_value_score(d: dict) -> float | None:
    """
    简易性价比评分 (0-10)
    - 成长性 (利润增速 + 营收增速) 40%
    - 质量 (ROE + 净利率) 30%
    - 技术面 (RSI居中 + MACD趋势) 20%
    - 估值 (PE合理性) 10%
    """
    fin = d.get("financials", {})
    kline = d.get("kline", {})
    rt = d.get("realtime", {})

    if not fin and not kline:
        return None

    score = 0.0
    weights_sum = 0.0

    # 成长性 (0-10, weight=4)
    growth = fin.get("net_profit_growth_pct")
    rev_growth = fin.get("revenue_growth_pct")
    if growth is not None and rev_growth is not None:
        growth_score = min(10, max(0, (growth + rev_growth) / 10))
        score += growth_score * 4
        weights_sum += 4

    # 质量 (0-10, weight=3)
    roe = fin.get("roe_pct")
    margin = fin.get("net_margin_pct")
    if roe is not None and margin is not None:
        quality_score = min(10, max(0, roe / 3 + margin / 5))
        score += quality_score * 3
        weights_sum += 3

    # 技术面 (0-10, weight=2)
    rsi = kline.get("rsi14")
    macd_h = kline.get("macd_hist")
    if rsi is not None:
        # RSI 在 40-60 之间得分高，超买超卖扣分
        rsi_score = max(0, 10 - abs(rsi - 50) / 5)
        tech_score = rsi_score
        if macd_h is not None:
            tech_score = (tech_score + (5 + min(5, max(-5, macd_h * 10)))) / 2
        score += tech_score * 2
        weights_sum += 2

    # 估值 (0-10, weight=1)
    pe = rt.get("pe_ratio") if rt else None
    if pe is not None and pe > 0:
        # PE 在 15-30 之间得分高
        if pe < 15:
            val_score = 8
        elif pe < 30:
            val_score = 6
        elif pe < 50:
            val_score = 4
        else:
            val_score = 2
        score += val_score * 1
        weights_sum += 1

    if weights_sum == 0:
        return None
    return round(score / weights_sum, 1)


def render_news_summary(all_data: list[dict]):
    """渲染新闻摘要"""
    for d in all_data:
        news = d.get("news", [])
        if not news:
            continue
        console.print(
            Panel(
                "\n".join(
                    f"  [{n.get('time', '')}] {n.get('title', '')}"
                    for n in news[:3]
                ),
                title=f"{d['symbol']} {d['name']} 最新新闻",
                border_style="blue",
            )
        )


# ──────────────────────────── 主流程 ────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AIDC 电力基建股票数据批量采集")
    parser.add_argument(
        "--symbols", type=str, default=None,
        help="逗号分隔的股票代码列表，留空则使用默认 watchlist",
    )
    parser.add_argument(
        "--interval", type=float, default=1.5,
        help="请求最小间隔（秒），默认 1.5",
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="快速模式：仅获取实时行情",
    )
    args = parser.parse_args()

    # 构建股票列表
    if args.symbols:
        symbols = {}
        for s in args.symbols.split(","):
            s = s.strip()
            symbols[s] = AIDC_WATCHLIST.get(s, s)
    else:
        symbols = AIDC_WATCHLIST

    limiter = RateLimiter(min_interval=args.interval)

    console.print(
        Panel(
            f"标的数量: {len(symbols)}  |  请求间隔: {args.interval}s  |  模式: {'快速' if args.quick else '完整'}",
            title="[bold]AIDC 电力基建数据采集[/bold]",
            border_style="cyan",
        )
    )

    all_data = []
    for idx, (symbol, name) in enumerate(symbols.items(), 1):
        console.rule(f"[bold]{idx}/{len(symbols)} {symbol} {name}[/bold]")

        item = {"symbol": symbol, "name": name}

        # 1. 实时行情（始终获取）
        item["realtime"] = fetch_realtime(symbol, name, limiter)

        if not args.quick:
            # 2. 历史K线 + 技术指标
            item["kline"] = fetch_hist_kline(symbol, limiter, days=60)

            # 3. 财务数据
            item["financials"] = fetch_financials(symbol, limiter)

            # 4. 新闻
            item["news"] = fetch_news(symbol, limiter, max_news=5)

        all_data.append(item)

    # ─── 渲染表格 ───
    console.print()
    render_realtime_table([d.get("realtime", {}) for d in all_data])

    if not args.quick:
        console.print()
        render_analysis_table(all_data)
        console.print()
        render_news_summary(all_data)

    # ─── 保存 JSON ───
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(data_dir, f"aidc_watchlist_{today}.json")

    # 序列化前处理
    output = {
        "fetch_time": datetime.now().isoformat(),
        "mode": "quick" if args.quick else "full",
        "stocks": all_data,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    console.print(f"\n[bold green]✓ 数据已保存至 {out_path}[/]")


if __name__ == "__main__":
    main()
