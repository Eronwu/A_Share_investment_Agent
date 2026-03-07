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


def _build_proxy_free_env() -> Dict[str, str]:
    """构造完全不继承代理配置的子进程环境变量"""
    clean_env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    clean_env["NO_PROXY"] = "*"
    clean_env["no_proxy"] = "*"
    return clean_env


def _run_eastmoney_curl(url: str, timeout: int = 15) -> Dict[str, Any]:
    """使用 curl 直连东方财富接口，显式绕过所有代理"""
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
    """返回东方财富 secid 的优先尝试顺序"""
    prefixes = ["0", "1"] if symbol.startswith(("0", "3")) else ["1", "0"]
    return [f"{prefix}.{symbol}" for prefix in prefixes]


def _get_market_symbol(symbol: str) -> str:
    """将纯数字股票代码转换为带市场前缀的代码"""
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("8", "4")):
        return f"bj{symbol}"
    return f"sz{symbol}"


def _get_sina_financial_symbol(symbol: str) -> str:
    """新浪财报接口使用的证券代码格式"""
    return _get_market_symbol(symbol)


def _fetch_history_from_sina_or_tx(
    symbol: str, start_date: datetime, end_date: datetime, adjust: str = "qfq"
) -> pd.DataFrame:
    """优先使用新浪，其次使用腾讯获取历史行情，绕开东方财富"""
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
    return pd.DataFrame()


def _get_latest_trade_snapshot(symbol: str) -> Dict[str, float]:
    """基于新浪/腾讯历史数据生成最新交易快照和估值基础数据"""
    end_date = datetime.now()
    start_date = end_date - timedelta(days=400)
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


def get_realtime_quotes_with_curl():
    """使用 curl 获取实时行情数据"""
    # 东财实时行情接口
    urls = [
        "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
        "https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048&fields=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
    ]
    for url in urls:
        try:
            data = _run_eastmoney_curl(url, timeout=10)
            if data and data.get("data") and "diff" in data["data"]:
                diff = data["data"]["diff"]
                df = pd.DataFrame(diff)
                column_mapping = {
                    "f5": "成交量",
                    "f12": "代码",
                    "f14": "名称",
                    "f15": "最高",
                    "f16": "最低",
                    "f2": "最新价",
                    "f3": "涨跌幅",
                    "f20": "总市值",
                    "f21": "流通市值",
                    "f23": "市净率",
                    "f115": "市盈率-动态",
                }
                df = df.rename(columns=column_mapping)
                return df
        except Exception as e:
            logger.error(f"Realtime curl fallback failed: {e}")
    return pd.DataFrame()

def get_financial_metrics(symbol: str) -> Dict[str, Any]:
    """获取财务指标数据"""
    logger.info(f"Getting financial indicators for {symbol}...")
    try:
        logger.info("Fetching latest trade snapshot from Sina/Tencent...")
        stock_data = _get_latest_trade_snapshot(symbol)
        logger.info("✓ Latest trade snapshot fetched")

        # 获取新浪财务指标
        logger.info("Fetching Sina financial indicators...")
        current_year = datetime.now().year
        financial_data = ak.stock_financial_analysis_indicator(
            symbol=symbol, start_year=str(current_year-1))
        if financial_data is None or financial_data.empty:
            logger.warning("No financial indicator data available")
            return [{}]

        # 按日期排序并获取最新的数据
        financial_data['日期'] = pd.to_datetime(financial_data['日期'])
        financial_data = financial_data.sort_values('日期', ascending=False)
        latest_financial = financial_data.iloc[0] if not financial_data.empty else pd.Series(
        )
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
            # A股没有平均成交量，暂用当日成交量
            "average_volume": float(stock_data.get("volume", 0)),
            "fifty_two_week_high": float(stock_data.get("fifty_two_week_high", 0)),
            "fifty_two_week_low": float(stock_data.get("fifty_two_week_low", 0))
        }

    except Exception as e:
        logger.warning(f"Sina/Tencent market data failed, trying Eastmoney/Akshare: {e}")
        try:
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
    """获取历史价格数据"""
    try:
        current_date = datetime.now()
        yesterday = current_date - timedelta(days=1)
        if not end_date:
            end_date = yesterday
        else:
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
            if end_date > yesterday:
                end_date = yesterday
        if not start_date:
            start_date = end_date - timedelta(days=365)
        else:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")

        logger.info(f"\nGetting price history for {symbol}...")
        
        def fetch_with_curl(symbol, start_date, end_date):
            """使用系统 curl 命令抓取数据，带环境隔离和多 secid 重试逻辑"""
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
                            columns = [
                                "date",
                                "open",
                                "close",
                                "high",
                                "low",
                            ]
                            if len(rows[0]) >= 11:
                                columns.extend(
                                    [
                                        "volume",
                                        "amount",
                                        "amplitude",
                                        "pct_change",
                                        "change_amount",
                                        "turnover",
                                    ]
                                )
                            df_new = pd.DataFrame(rows, columns=columns)
                            numeric_cols = [
                                col
                                for col in [
                                    "open",
                                    "close",
                                    "high",
                                    "low",
                                    "volume",
                                    "amount",
                                    "amplitude",
                                    "pct_change",
                                    "change_amount",
                                    "turnover",
                                ]
                                if col in df_new.columns
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
            try:
                df = _fetch_history_from_sina_or_tx(symbol, start_date, end_date, adjust=adjust)
                if df is None or df.empty:
                    raise ValueError("empty price history from Sina/Tencent")
            except Exception as e:
                logger.warning(f"Sina/Tencent price history failed, trying Eastmoney: {e}")
                try:
                    df = fetch_with_curl(symbol, start_date, end_date)
                except Exception:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol, period="daily",
                        start_date=start_date.strftime("%Y%m%d"),
                        end_date=end_date.strftime("%Y%m%d"), adjust=adjust
                    )

            if df is None or df.empty:
                df = fetch_with_curl(symbol, start_date, end_date)
                if df is None or df.empty:
                    return pd.DataFrame()

            # 统一列名
            rename_map = {"日期": "date", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume", "成交额": "amount", "振幅": "amplitude", "涨跌幅": "pct_change", "涨跌额": "change_amount", "换手率": "turnover"}
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            df["date"] = pd.to_datetime(df["date"])
            return df

        df = get_and_process_data(start_date, end_date)
        if df.empty:
            logger.warning(f"Warning: No price history data found for {symbol}")
            return pd.DataFrame()

        # 计算技术指标
        df = df.sort_values("date").reset_index(drop=True)
        df["momentum_1m"] = df["close"].pct_change(periods=20)
        df["momentum_3m"] = df["close"].pct_change(periods=60)
        df["momentum_6m"] = df["close"].pct_change(periods=120)
        df["volume_ma20"] = df["volume"].rolling(window=20).mean()
        df["volume_momentum"] = df["volume"] / df["volume_ma20"]
        returns = df["close"].pct_change()
        df["historical_volatility"] = returns.rolling(window=20).std() * np.sqrt(252)
        
        # 补全其他指标... (简化版)
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
