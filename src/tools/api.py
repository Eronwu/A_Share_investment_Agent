from typing import Dict, Any, List
import pandas as pd
import akshare as ak
from datetime import datetime, timedelta
import json
import numpy as np
import os
import subprocess
from src.utils.logging_config import setup_logger

# 设置日志记录
logger = setup_logger('api')

EASTMONEY_REFERER = "https://quote.eastmoney.com/"
EASTMONEY_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def get_stock_prefix(symbol: str) -> str:
    """根据股票代码获取所属交易所前缀"""
    if symbol.startswith(('60', '68')):
        return 'sh'
    elif symbol.startswith(('00', '30')):
        return 'sz'
    elif symbol.startswith(('43', '83', '87', '88')):
        return 'bj'
    return 'sh'  # 默认返回sh


def _build_proxy_free_env() -> Dict[str, str]:
    clean_env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    clean_env["NO_PROXY"] = "*"
    clean_env["no_proxy"] = "*"
    return clean_env


def _run_eastmoney_curl(url: str, timeout: int = 15) -> Dict[str, Any]:
    cmd = [
        "curl",
        "--silent",
        "--show-error",
        "--http1.1",
        "-H",
        f"Referer: {EASTMONEY_REFERER}",
        "-H",
        f"User-Agent: {EASTMONEY_USER_AGENT}",
        "-H",
        "Accept: */*",
        url,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_build_proxy_free_env(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or f"curl exited with {result.returncode} for {url}"
        )
    return json.loads(result.stdout)


def _eastmoney_secids(symbol: str) -> List[str]:
    prefixes = ["0", "1"] if symbol.startswith(("0", "3")) else ["1", "0"]
    return [f"{prefix}.{symbol}" for prefix in prefixes]


def _get_market_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("8", "4")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _get_sina_financial_symbol(symbol: str) -> str:
    return _get_market_symbol(symbol)


def _is_etf_symbol(symbol: str) -> bool:
    return symbol.startswith(("15", "16", "50", "51", "56", "58"))


def _fetch_etf_history(symbol: str, start_date: datetime, end_date: datetime, adjust: str = "qfq") -> pd.DataFrame:
    df = ak.fund_etf_hist_em(
        symbol=symbol,
        period="daily",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=end_date.strftime("%Y%m%d"),
        adjust=adjust,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change_amount",
        "换手率": "turnover",
    }).copy()
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "close", "high", "low", "volume", "amount", "amplitude", "pct_change", "change_amount", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _fetch_history_from_sina_or_tx(
    symbol: str, start_date: datetime, end_date: datetime, adjust: str = "qfq"
) -> pd.DataFrame:
    market_symbol = _get_market_symbol(symbol)
    normalized_adjust = adjust if adjust in {"", "qfq", "hfq"} else ""
    start_str = start_date.strftime("%Y%m%d")
    end_str = end_date.strftime("%Y%m%d")

    try:
        df = ak.stock_zh_a_daily(
            symbol=market_symbol,
            start_date=start_str,
            end_date=end_str,
            adjust=normalized_adjust,
        )
        if df is not None and not df.empty:
            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            return df
    except Exception as e:
        logger.warning(f"Sina history fetch failed for {symbol}: {e}")

    try:
        df = ak.stock_zh_a_hist_tx(
            symbol=market_symbol,
            start_date=start_str,
            end_date=end_str,
            adjust=normalized_adjust,
        )
        if df is not None and not df.empty:
            df = df.copy()
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            if "amount" in df.columns and "volume" not in df.columns:
                df["volume"] = pd.to_numeric(df["amount"], errors="coerce")
            return df
    except Exception as e:
        logger.warning(f"Tencent history fetch failed for {symbol}: {e}")

    return pd.DataFrame()


def _get_latest_trade_snapshot(symbol: str) -> Dict[str, float]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=400)
    if _is_etf_symbol(symbol):
        history_df = _fetch_etf_history(symbol, start_date, end_date, adjust="qfq")
    else:
        history_df = _fetch_history_from_sina_or_tx(symbol, start_date, end_date, adjust="qfq")
    if history_df is None or history_df.empty:
        raise ValueError(f"no history snapshot available for {symbol}")

    history_df = history_df.sort_values("date").reset_index(drop=True)
    latest_row = history_df.iloc[-1]
    latest_close = float(latest_row.get("close", 0) or 0)
    latest_high = float(latest_row.get("high", 0) or 0)
    latest_low = float(latest_row.get("low", 0) or 0)
    latest_open = float(latest_row.get("open", 0) or 0)
    latest_volume = float(latest_row.get("volume", 0) or 0)
    outstanding_share = float(latest_row.get("outstanding_share", 0) or 0)
    market_cap = latest_close * outstanding_share if outstanding_share > 0 else 0.0
    fifty_two_week_high = float(history_df["high"].tail(252).max()) if "high" in history_df.columns else latest_high
    fifty_two_week_low = float(history_df["low"].tail(252).min()) if "low" in history_df.columns else latest_low

    return {
        "open": latest_open,
        "close": latest_close,
        "high": latest_high,
        "low": latest_low,
        "volume": latest_volume,
        "market_cap": market_cap,
        "float_market_cap": market_cap,
        "fifty_two_week_high": fifty_two_week_high,
        "fifty_two_week_low": fifty_two_week_low,
        "outstanding_share": outstanding_share,
    }


# 全局缓存实时行情数据，避免频繁调用耗时的全市场接口
_REALTIME_CACHE = {
    "data": None,
    "timestamp": None
}
_CACHE_DURATION = timedelta(minutes=30)


def get_realtime_quotes(symbol: str = None):
    """获取实时行情数据（带缓存）
    如果提供了 symbol，则优先使用单股接口以提高速度
    """
    global _REALTIME_CACHE
    now = datetime.now()
    
    # 如果有全量缓存且未过期，直接使用
    if (_REALTIME_CACHE["data"] is not None and 
        _REALTIME_CACHE["timestamp"] is not None and 
        now - _REALTIME_CACHE["timestamp"] < _CACHE_DURATION):
        logger.info("Using cached real-time quotes")
        if symbol:
            stock_data = _REALTIME_CACHE["data"][_REALTIME_CACHE["data"]['代码'] == symbol]
            if not stock_data.empty:
                return stock_data
        return _REALTIME_CACHE["data"]
    
    # 如果提供了 symbol，尝试使用单股接口（极快）
    if symbol:
        logger.info(f"Fetching real-time data for {symbol} using individual info interface...")
        try:
            ind_info = ak.stock_individual_info_em(symbol=symbol)
            if ind_info is not None and not ind_info.empty:
                info_dict = dict(zip(ind_info['item'], ind_info['value']))
                # 构造一个与 stock_zh_a_spot_em 格式兼容的 Series/DataFrame
                stock_data = pd.DataFrame([{
                    '代码': symbol,
                    '名称': info_dict.get('股票简称', ''),
                    '最新': float(info_dict.get('最新', 0)),
                    '总市值': float(info_dict.get('总市值', 0)),
                    '流通市值': float(info_dict.get('流通市值', 0)),
                }])
                logger.info(f"✓ Quick real-time data fetched for {symbol}")
                return stock_data
        except Exception as e:
            logger.warning(f"Quick real-time data fetch failed for {symbol}: {e}")

    # 回退到全量获取（耗时）
    logger.info("Fetching real-time quotes for all A-shares (this may take a few minutes)...")
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            _REALTIME_CACHE["data"] = df
            _REALTIME_CACHE["timestamp"] = now
            logger.info(f"✓ Real-time quotes updated ({len(df)} stocks)")
            if symbol:
                return df[df['代码'] == symbol]
            return df
    except Exception as e:
        logger.error(f"Error fetching real-time quotes: {e}")
    
    return _REALTIME_CACHE["data"] # 返回旧数据或None


def get_financial_metrics(symbol: str) -> Dict[str, Any]:
    """获取财务指标数据"""
    logger.info(f"Getting financial indicators for {symbol}...")
    try:
        logger.info("Fetching latest trade snapshot from Sina/Tencent...")
        stock_data = _get_latest_trade_snapshot(symbol)
        logger.info("✓ Latest trade snapshot fetched")

        # 获取新浪财务指标
        logger.info(f"Fetching Sina financial indicators for {symbol}...")
        current_date = datetime.now()
        financial_data = ak.stock_financial_analysis_indicator(
            symbol=symbol, start_year=str(current_date.year-1))
        if financial_data is None or financial_data.empty:
            logger.warning("No financial indicator data available")
            return [{}]

        # 按日期排序并获取最新的数据
        financial_data['日期'] = pd.to_datetime(financial_data['日期'])
        financial_data = financial_data.sort_values('日期', ascending=False)
        latest_financial = financial_data.iloc[0] if not financial_data.empty else pd.Series()
        logger.info(
            f"✓ Financial indicators fetched ({len(financial_data)} records)")
        logger.info(f"Latest data date: {latest_financial.get('日期')}")

        # 获取利润表数据（用于计算 price_to_sales）
        logger.info("Fetching income statement...")
        try:
            income_statement = ak.stock_financial_report_sina(
                stock=_get_sina_financial_symbol(symbol), symbol="利润表")
            if not income_statement.empty:
                latest_income = income_statement.iloc[0]
                logger.info("✓ Income statement fetched")
            else:
                logger.warning("Failed to get income statement")
                logger.error("No income statement data found")
                latest_income = pd.Series()
        except Exception as e:
            logger.warning("Failed to get income statement")
            logger.error(f"Error getting income statement: {e}")
            latest_income = pd.Series()

        # 构建完整指标数据
        logger.info("Building indicators...")
        try:
            def convert_percentage(value: float) -> float:
                """将百分比值转换为小数"""
                try:
                    return float(value) / 100.0 if value is not None else 0.0
                except:
                    return 0.0

            def safe_float(value: Any) -> float:
                try:
                    if pd.isna(value):
                        return 0.0
                    return float(value)
                except Exception:
                    return 0.0

            latest_price = safe_float(stock_data.get("close", 0))
            book_value_per_share = safe_float(
                latest_financial.get("每股净资产_调整后(元)", latest_financial.get("每股净资产_调整前(元)", 0))
            )
            earnings_per_share = safe_float(
                latest_financial.get("加权每股收益(元)", latest_financial.get("摊薄每股收益(元)", 0))
            )
            market_cap = safe_float(stock_data.get("market_cap", 0))

            all_metrics = {
                # 市场数据
                "market_cap": market_cap,
                "float_market_cap": safe_float(stock_data.get("float_market_cap", market_cap)),

                # 盈利数据
                "revenue": float(latest_income.get("营业总收入", 0)),
                "net_income": float(latest_income.get("净利润", 0)),
                "return_on_equity": convert_percentage(latest_financial.get("净资产收益率(%)", 0)),
                "net_margin": convert_percentage(latest_financial.get("销售净利率(%)", 0)),
                "operating_margin": convert_percentage(latest_financial.get("营业利润率(%)", 0)),

                # 增长指标
                "revenue_growth": convert_percentage(latest_financial.get("主营业务收入增长率(%)", 0)),
                "earnings_growth": convert_percentage(latest_financial.get("净利润增长率(%)", 0)),
                "book_value_growth": convert_percentage(latest_financial.get("净资产增长率(%)", 0)),

                # 财务健康指标
                "current_ratio": float(latest_financial.get("流动比率", 0)),
                "debt_to_equity": convert_percentage(latest_financial.get("资产负债率(%)", 0)),
                "free_cash_flow_per_share": float(latest_financial.get("每股经营性现金流(元)", 0)),
                "earnings_per_share": earnings_per_share,

                # 估值比率
                "pe_ratio": latest_price / earnings_per_share if earnings_per_share > 0 else 0,
                "price_to_book": latest_price / book_value_per_share if book_value_per_share > 0 else 0,
                "price_to_sales": market_cap / float(latest_income.get("营业总收入", 1)) if float(latest_income.get("营业总收入", 0)) > 0 else 0,
            }

            # 只返回 agent 需要的指标
            agent_metrics = {
                # 盈利能力指标
                "return_on_equity": all_metrics["return_on_equity"],
                "net_margin": all_metrics["net_margin"],
                "operating_margin": all_metrics["operating_margin"],

                # 增长指标
                "revenue_growth": all_metrics["revenue_growth"],
                "earnings_growth": all_metrics["earnings_growth"],
                "book_value_growth": all_metrics["book_value_growth"],

                # 财务健康指标
                "current_ratio": all_metrics["current_ratio"],
                "debt_to_equity": all_metrics["debt_to_equity"],
                "free_cash_flow_per_share": all_metrics["free_cash_flow_per_share"],
                "earnings_per_share": all_metrics["earnings_per_share"],

                # 估值比率
                "pe_ratio": all_metrics["pe_ratio"],
                "price_to_book": all_metrics["price_to_book"],
                "price_to_sales": all_metrics["price_to_sales"],
            }

            logger.info("✓ Indicators built successfully")

            # 打印所有获取到的指标数据（用于调试）
            logger.debug("\n获取到的完整指标数据：")
            for key, value in all_metrics.items():
                logger.debug(f"{key}: {value}")

            logger.debug("\n传递给 agent 的指标数据：")
            for key, value in agent_metrics.items():
                logger.debug(f"{key}: {value}")

            return [agent_metrics]

        except Exception as e:
            logger.error(f"Error building indicators: {e}")
            return [{}]

    except Exception as e:
        logger.error(f"Error getting financial indicators: {e}")
        return [{}]


def get_financial_statements(symbol: str) -> Dict[str, Any]:
    """获取财务报表数据"""
    logger.info(f"Getting financial statements for {symbol}...")
    try:
        # 获取资产负债表数据
        logger.info("Fetching balance sheet...")
        try:
            balance_sheet = ak.stock_financial_report_sina(
                stock=_get_sina_financial_symbol(symbol), symbol="资产负债表")
            if not balance_sheet.empty:
                latest_balance = balance_sheet.iloc[0]
                previous_balance = balance_sheet.iloc[1] if len(
                    balance_sheet) > 1 else balance_sheet.iloc[0]
                logger.info("✓ Balance sheet fetched")
            else:
                logger.warning("Failed to get balance sheet")
                logger.error("No balance sheet data found")
                latest_balance = pd.Series()
                previous_balance = pd.Series()
        except Exception as e:
            logger.warning("Failed to get balance sheet")
            logger.error(f"Error getting balance sheet: {e}")
            latest_balance = pd.Series()
            previous_balance = pd.Series()

        # 获取利润表数据
        logger.info("Fetching income statement...")
        try:
            income_statement = ak.stock_financial_report_sina(
                stock=_get_sina_financial_symbol(symbol), symbol="利润表")
            if not income_statement.empty:
                latest_income = income_statement.iloc[0]
                previous_income = income_statement.iloc[1] if len(
                    income_statement) > 1 else income_statement.iloc[0]
                logger.info("✓ Income statement fetched")
            else:
                logger.warning("Failed to get income statement")
                logger.error("No income statement data found")
                latest_income = pd.Series()
                previous_income = pd.Series()
        except Exception as e:
            logger.warning("Failed to get income statement")
            logger.error(f"Error getting income statement: {e}")
            latest_income = pd.Series()
            previous_income = pd.Series()

        # 获取现金流量表数据
        logger.info("Fetching cash flow statement...")
        try:
            cash_flow = ak.stock_financial_report_sina(
                stock=_get_sina_financial_symbol(symbol), symbol="现金流量表")
            if not cash_flow.empty:
                latest_cash_flow = cash_flow.iloc[0]
                previous_cash_flow = cash_flow.iloc[1] if len(
                    cash_flow) > 1 else cash_flow.iloc[0]
                logger.info("✓ Cash flow statement fetched")
            else:
                logger.warning("Failed to get cash flow statement")
                logger.error("No cash flow data found")
                latest_cash_flow = pd.Series()
                previous_cash_flow = pd.Series()
        except Exception as e:
            logger.warning("Failed to get cash flow statement")
            logger.error(f"Error getting cash flow statement: {e}")
            latest_cash_flow = pd.Series()
            previous_cash_flow = pd.Series()

        # 构建财务数据
        line_items = []
        try:
            # 处理最新期间数据
            current_item = {
                # 从利润表获取
                "net_income": float(latest_income.get("净利润", 0)),
                "operating_revenue": float(latest_income.get("营业总收入", 0)),
                "operating_profit": float(latest_income.get("营业利润", 0)),

                # 从资产负债表计算营运资金
                "working_capital": float(latest_balance.get("流动资产合计", 0)) - float(latest_balance.get("流动负债合计", 0)),

                # 从现金流量表获取
                "depreciation_and_amortization": float(latest_cash_flow.get("固定资产折旧、油气资产折耗、生产性生物资产折旧", 0)),
                "capital_expenditure": abs(float(latest_cash_flow.get("购建固定资产、无形资产和其他长期资产支付的现金", 0))),
                "free_cash_flow": float(latest_cash_flow.get("经营活动产生的现金流量净额", 0)) - abs(float(latest_cash_flow.get("购建固定资产、无形资产和其他长期资产支付的现金", 0)))
            }
            line_items.append(current_item)
            logger.info("✓ Latest period data processed successfully")

            # 处理上一期间数据
            previous_item = {
                "net_income": float(previous_income.get("净利润", 0)),
                "operating_revenue": float(previous_income.get("营业总收入", 0)),
                "operating_profit": float(previous_income.get("营业利润", 0)),
                "working_capital": float(previous_balance.get("流动资产合计", 0)) - float(previous_balance.get("流动负债合计", 0)),
                "depreciation_and_amortization": float(previous_cash_flow.get("固定资产折旧、油气资产折耗、生产性生物资产折旧", 0)),
                "capital_expenditure": abs(float(previous_cash_flow.get("购建固定资产、无形资产和其他长期资产支付的现金", 0))),
                "free_cash_flow": float(previous_cash_flow.get("经营活动产生的现金流量净额", 0)) - abs(float(previous_cash_flow.get("购建固定资产、无形资产和其他长期资产支付的现金", 0)))
            }
            line_items.append(previous_item)
            logger.info("✓ Previous period data processed successfully")

        except Exception as e:
            logger.error(f"Error processing financial data: {e}")
            default_item = {
                "net_income": 0,
                "operating_revenue": 0,
                "operating_profit": 0,
                "working_capital": 0,
                "depreciation_and_amortization": 0,
                "capital_expenditure": 0,
                "free_cash_flow": 0
            }
            line_items = [default_item, default_item]

        return line_items

    except Exception as e:
        logger.error(f"Error getting financial statements: {e}")
        default_item = {
            "net_income": 0,
            "operating_revenue": 0,
            "operating_profit": 0,
            "working_capital": 0,
            "depreciation_and_amortization": 0,
            "capital_expenditure": 0,
            "free_cash_flow": 0
        }
        return [default_item, default_item]


def get_market_data(symbol: str) -> Dict[str, Any]:
    """获取市场数据"""
    try:
        stock_data = _get_latest_trade_snapshot(symbol)
        return {
            "market_cap": float(stock_data.get("market_cap", 0)),
            "volume": float(stock_data.get("volume", 0)),
            "average_volume": float(stock_data.get("volume", 0)),
            "fifty_two_week_high": float(stock_data.get("fifty_two_week_high", 0)),
            "fifty_two_week_low": float(stock_data.get("fifty_two_week_low", 0))
        }

    except Exception as e:
        logger.warning(f"Sina/Tencent market data failed, trying Eastmoney/Akshare: {e}")
        try:
            if _is_etf_symbol(symbol):
                realtime_data = ak.fund_etf_spot_em()
            else:
                realtime_data = ak.stock_zh_a_spot_em()
            stock_data = realtime_data[realtime_data['代码'] == symbol].iloc[0]
            return {
                "market_cap": float(stock_data.get("总市值", 0)),
                "volume": float(stock_data.get("成交量", 0)),
                "average_volume": float(stock_data.get("成交量", 0)),
                "fifty_two_week_high": float(stock_data.get("52周最高", 0)),
                "fifty_two_week_low": float(stock_data.get("52周最低", 0)),
            }
        except Exception as inner_e:
            logger.error(f"Error getting market data: {inner_e}")
            return {}


def get_price_history(symbol: str, start_date: str = None, end_date: str = None, adjust: str = "qfq") -> pd.DataFrame:
    """获取历史价格数据

    Args:
        symbol: 股票代码
        start_date: 开始日期，格式：YYYY-MM-DD，如果为None则默认获取过去一年的数据
        end_date: 结束日期，格式：YYYY-MM-DD，如果为None则使用昨天作为结束日期
        adjust: 复权类型，可选值：
               - "": 不复权
               - "qfq": 前复权（默认）
               - "hfq": 后复权

    Returns:
        包含以下列的DataFrame：
        - date: 日期
        - open: 开盘价
        - high: 最高价
        - low: 最低价
        - close: 收盘价
        - volume: 成交量（手）
        - amount: 成交额（元）
        - amplitude: 振幅（%）
        - pct_change: 涨跌幅（%）
        - change_amount: 涨跌额（元）
        - turnover: 换手率（%）

        技术指标：
        - momentum_1m: 1个月动量
        - momentum_3m: 3个月动量
        - momentum_6m: 6个月动量
        - volume_momentum: 成交量动量
        - historical_volatility: 历史波动率
        - volatility_regime: 波动率区间
        - volatility_z_score: 波动率Z分数
        - atr_ratio: 真实波动幅度比率
        - hurst_exponent: 赫斯特指数
        - skewness: 偏度
        - kurtosis: 峰度
    """
    try:
        # 获取当前日期和昨天的日期
        current_date = datetime.now()
        yesterday = current_date - timedelta(days=1)

        # 如果没有提供日期，默认使用昨天作为结束日期
        if not end_date:
            end_date = yesterday  # 使用昨天作为结束日期
        else:
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
            # 确保end_date不会超过昨天
            if end_date > yesterday:
                end_date = yesterday

        if not start_date:
            start_date = end_date - timedelta(days=365)  # 默认获取一年的数据
        else:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")

        logger.info(f"\nGetting price history for {symbol}...")
        logger.info(f"Start date: {start_date.strftime('%Y-%m-%d')}")
        logger.info(f"End date: {end_date.strftime('%Y-%m-%d')}")

        def fetch_with_curl(symbol, start_date, end_date):
            for secid in _eastmoney_secids(symbol):
                urls = [
                    (
                        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                        f"?secid={secid}"
                        "&fields1=f1,f2,f3"
                        "&fields2=f51,f52,f53,f54,f55"
                        "&klt=101&fqt=1"
                        f"&beg={start_date.strftime('%Y%m%d')}"
                        f"&end={end_date.strftime('%Y%m%d')}"
                    ),
                    (
                        "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                        f"?secid={secid}"
                        "&fields1=f1,f2,f3"
                        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
                        "&klt=101&fqt=1"
                        f"&beg={start_date.strftime('%Y%m%d')}"
                        f"&end={end_date.strftime('%Y%m%d')}"
                    ),
                ]
                for url in urls:
                    try:
                        data = _run_eastmoney_curl(url, timeout=15)
                        if data and data.get("data") and "klines" in data["data"]:
                            klines = data["data"]["klines"]
                            rows = [k.split(',') for k in klines]
                            columns = ["date", "open", "close", "high", "low"]
                            if rows and len(rows[0]) >= 11:
                                columns.extend([
                                    "volume",
                                    "amount",
                                    "amplitude",
                                    "pct_change",
                                    "change_amount",
                                    "turnover",
                                ])
                            df_new = pd.DataFrame(rows, columns=columns)
                            numeric_cols = [
                                col for col in [
                                    "open", "close", "high", "low", "volume",
                                    "amount", "amplitude", "pct_change", "change_amount", "turnover",
                                ] if col in df_new.columns
                            ]
                            for col in numeric_cols:
                                df_new[col] = pd.to_numeric(df_new[col], errors="coerce")
                            df_new["date"] = pd.to_datetime(df_new["date"])
                            logger.info(
                                f"✓ Successfully fetched data for {secid} via direct Eastmoney curl"
                            )
                            return df_new
                    except Exception as e:
                        logger.error(f"Curl fallback attempt ({secid}) failed: {e}")
            return pd.DataFrame()

        def get_and_process_data(start_date, end_date):
            """获取并处理数据，包括重命名列等操作"""
            try:
                if _is_etf_symbol(symbol):
                    df = _fetch_etf_history(symbol, start_date, end_date, adjust=adjust)
                else:
                    df = _fetch_history_from_sina_or_tx(symbol, start_date, end_date, adjust=adjust)
                if df is None or df.empty:
                    raise ValueError("empty price history from primary source")
            except Exception as e:
                logger.warning(f"Primary price history failed, trying Eastmoney: {e}")
                try:
                    df = fetch_with_curl(symbol, start_date, end_date)
                except Exception:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date.strftime("%Y%m%d"),
                        end_date=end_date.strftime("%Y%m%d"),
                        adjust=adjust
                    )

            if df is None or df.empty:
                df = fetch_with_curl(symbol, start_date, end_date)
                if df is None or df.empty:
                    return pd.DataFrame()

            # 重命名列以匹配技术分析代理的需求
            df = df.rename(columns={
                "日期": "date",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "pct_change",
                "涨跌额": "change_amount",
                "换手率": "turnover"
            })

            # 确保日期列为datetime类型
            df["date"] = pd.to_datetime(df["date"])
            return df

        # 获取历史行情数据
        df = get_and_process_data(start_date, end_date)

        if df is None or df.empty:
            logger.warning(
                f"Warning: No price history data found for {symbol}")
            return pd.DataFrame()

        # 检查数据量是否足够
        min_required_days = 120  # 至少需要120个交易日的数据
        if len(df) < min_required_days:
            logger.warning(
                f"Warning: Insufficient data ({len(df)} days) for all technical indicators")
            logger.info("Attempting to fetch more data...")

            # 扩大时间范围到2年
            start_date = end_date - timedelta(days=730)
            df = get_and_process_data(start_date, end_date)

            if len(df) < min_required_days:
                logger.warning(
                    f"Warning: Even with extended time range, insufficient data ({len(df)} days)")

        # 计算动量指标
        df["momentum_1m"] = df["close"].pct_change(periods=20)  # 20个交易日约等于1个月
        df["momentum_3m"] = df["close"].pct_change(periods=60)  # 60个交易日约等于3个月
        df["momentum_6m"] = df["close"].pct_change(
            periods=120)  # 120个交易日约等于6个月

        # 计算成交量动量（相对于20日平均成交量的变化）
        df["volume_ma20"] = df["volume"].rolling(window=20).mean()
        df["volume_momentum"] = df["volume"] / df["volume_ma20"]

        # 计算波动率指标
        # 1. 历史波动率 (20日)
        returns = df["close"].pct_change()
        df["historical_volatility"] = returns.rolling(
            window=20).std() * np.sqrt(252)  # 年化

        # 2. 波动率区间 (相对于过去120天的波动率的位置)
        volatility_120d = returns.rolling(window=120).std() * np.sqrt(252)
        vol_min = volatility_120d.rolling(window=120).min()
        vol_max = volatility_120d.rolling(window=120).max()
        vol_range = vol_max - vol_min
        df["volatility_regime"] = np.where(
            vol_range > 0,
            (df["historical_volatility"] - vol_min) / vol_range,
            0  # 当范围为0时返回0
        )

        # 3. 波动率Z分数
        vol_mean = df["historical_volatility"].rolling(window=120).mean()
        vol_std = df["historical_volatility"].rolling(window=120).std()
        df["volatility_z_score"] = (
            df["historical_volatility"] - vol_mean) / vol_std

        # 4. ATR比率
        tr = pd.DataFrame()
        tr["h-l"] = df["high"] - df["low"]
        tr["h-pc"] = abs(df["high"] - df["close"].shift(1))
        tr["l-pc"] = abs(df["low"] - df["close"].shift(1))
        tr["tr"] = tr[["h-l", "h-pc", "l-pc"]].max(axis=1)
        df["atr"] = tr["tr"].rolling(window=14).mean()
        df["atr_ratio"] = df["atr"] / df["close"]

        # 计算统计套利指标
        # 1. 赫斯特指数 (使用过去120天的数据)
        def calculate_hurst(series):
            """
            计算Hurst指数。

            Args:
                series: 价格序列

            Returns:
                float: Hurst指数，或在计算失败时返回np.nan
            """
            try:
                series = series.dropna()
                if len(series) < 30:  # 降低最小数据点要求
                    return np.nan

                # 使用对数收益率
                log_returns = np.log(series / series.shift(1)).dropna()
                if len(log_returns) < 30:  # 降低最小数据点要求
                    return np.nan

                # 使用更小的lag范围
                # 减少lag范围到2-10天
                lags = range(2, min(11, len(log_returns) // 4))

                # 计算每个lag的标准差
                tau = []
                for lag in lags:
                    # 计算滚动标准差
                    std = log_returns.rolling(window=lag).std().dropna()
                    if len(std) > 0:
                        tau.append(np.mean(std))

                # 基本的数值检查
                if len(tau) < 3:  # 进一步降低最小要求
                    return np.nan

                # 使用对数回归
                lags_log = np.log(list(lags))
                tau_log = np.log(tau)

                # 计算回归系数
                reg = np.polyfit(lags_log, tau_log, 1)
                hurst = reg[0] / 2.0

                # 只保留基本的数值检查
                if np.isnan(hurst) or np.isinf(hurst):
                    return np.nan

                return hurst

            except Exception as e:
                return np.nan

        # 使用对数收益率计算Hurst指数
        log_returns = np.log(df["close"] / df["close"].shift(1))
        df["hurst_exponent"] = log_returns.rolling(
            window=120,
            min_periods=60  # 要求至少60个数据点
        ).apply(calculate_hurst)

        # 2. 偏度 (20日)
        df["skewness"] = returns.rolling(window=20).skew()

        # 3. 峰度 (20日)
        df["kurtosis"] = returns.rolling(window=20).kurt()

        # 按日期升序排序
        df = df.sort_values("date")

        # 重置索引
        df = df.reset_index(drop=True)

        logger.info(
            f"Successfully fetched price history data ({len(df)} records)")

        # 检查并报告NaN值
        nan_columns = df.isna().sum()
        if nan_columns.any():
            logger.warning(
                "\nWarning: The following indicators contain NaN values:")
            for col, nan_count in nan_columns[nan_columns > 0].items():
                logger.warning(f"- {col}: {nan_count} records")

        return df

    except Exception as e:
        logger.error(f"Error getting price history: {e}")
        return pd.DataFrame()


def prices_to_df(prices):
    """Convert price data to DataFrame with standardized column names"""
    try:
        df = pd.DataFrame(prices)

        # 标准化列名映射
        column_mapping = {
            '收盘': 'close',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
            '振幅': 'amplitude',
            '涨跌幅': 'change_percent',
            '涨跌额': 'change_amount',
            '换手率': 'turnover_rate'
        }

        # 重命名列
        for cn, en in column_mapping.items():
            if cn in df.columns:
                df[en] = df[cn]

        # 确保必要的列存在
        required_columns = ['close', 'open', 'high', 'low', 'volume']
        for col in required_columns:
            if col not in df.columns:
                df[col] = 0.0  # 使用0填充缺失的必要列

        return df
    except Exception as e:
        logger.error(f"Error converting price data: {str(e)}")
        # 返回一个包含必要列的空DataFrame
        return pd.DataFrame(columns=['close', 'open', 'high', 'low', 'volume'])


def get_price_data(
    ticker: str,
    start_date: str,
    end_date: str
) -> pd.DataFrame:
    """获取股票价格数据

    Args:
        ticker: 股票代码
        start_date: 开始日期，格式：YYYY-MM-DD
        end_date: 结束日期，格式：YYYY-MM-DD

    Returns:
        包含价格数据的DataFrame
    """
    return get_price_history(ticker, start_date, end_date)
