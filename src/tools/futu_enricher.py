"""
Futu L2 数据增强器 — 可选依赖，失败不影响主流程

为 market_data_poller 提供 Tier-1 L2 衍生信号:
  - mainNetInflow    主力净流入（超大单+大单）
  - mainNetInflowPct 主力净流入占成交额比例%
  - retailNetInflow  散户净流入（小单）
  - bidAskRatio      委比
  - avgPrice         当日均价

设计原则:
  - Lazy connect: 首次调用时才连接 OpenD，不阻塞 poller 启动
  - Graceful degradation: 任何 Futu 调用失败 → 返回空 dict，poller 照常运行
  - Auto reconnect: 连接断开后下次调用自动重连
  - 不消耗订阅配额: capital_flow + snapshot 都是 one-shot 查询
"""

import socket
import time
from typing import Optional

from src.utils.logging_config import setup_logger

logger = setup_logger("futu_enricher")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111

# 连接失败后的冷却时间（秒），避免每轮 poll 都尝试连接
RECONNECT_COOLDOWN = 60


# ── 代码映射（复用 futu_quote_demo 的逻辑） ───────────────


def to_futu_code(code: str) -> str:
    """本项目代码 → Futu 格式"""
    if code.startswith("HK"):
        return f"HK.{code[2:]}"
    first = code[0]
    if first in ("6", "5"):
        return f"SH.{code}"
    if first in ("0", "1", "2", "3"):
        return f"SZ.{code}"
    return f"SH.{code}"


def from_futu_code(futu_code: str) -> str:
    """Futu 格式 → 本项目代码"""
    market, num = futu_code.split(".", 1)
    if market == "HK":
        return f"HK{num}"
    return num


# ── Enricher ─────────────────────────────────────────────


class FutuL2Enricher:
    """Futu L2 可选增强器

    用法:
        enricher = FutuL2Enricher()
        # 在 poll_once 中:
        l2_data = enricher.enrich(services)
        # l2_data = {"HK00700": {"mainNetInflow": 6.35e8, ...}, ...}
        # 如果 Futu 不可用，l2_data = {}
    """

    def __init__(self, host: str = OPEND_HOST, port: int = OPEND_PORT):
        self._host = host
        self._port = port
        self._ctx = None
        self._last_fail_time: float = 0

    def _is_port_open(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((self._host, self._port)) == 0
        sock.close()
        return result

    def _ensure_connected(self) -> bool:
        """懒连接 + 冷却期控制"""
        if self._ctx is not None:
            return True

        # 冷却期内不重试
        if time.time() - self._last_fail_time < RECONNECT_COOLDOWN:
            return False

        if not self._is_port_open():
            self._last_fail_time = time.time()
            logger.debug("OpenD 未运行，跳过 L2 增强")
            return False

        try:
            from futu import OpenQuoteContext
            self._ctx = OpenQuoteContext(host=self._host, port=self._port)
            logger.info("Futu OpenD 连接成功，L2 增强已启用")
            return True
        except Exception as e:
            self._last_fail_time = time.time()
            logger.warning(f"Futu 连接失败: {e}")
            return False

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    def enrich(self, services: list[dict]) -> dict[str, dict]:
        """为 services 列表中的每只股票获取 L2 衍生信号

        Returns:
            {stock_code: {field: value, ...}, ...}
            失败时返回空 dict，不影响调用方。
        """
        if not self._ensure_connected():
            return {}

        try:
            return self._do_enrich(services)
        except Exception as e:
            logger.warning(f"L2 增强失败: {e}")
            # 连接可能已断开，清理以便下次重连
            self.close()
            self._last_fail_time = time.time()
            return {}

    def _do_enrich(self, services: list[dict]) -> dict[str, dict]:
        from futu import RET_OK

        result = {}

        # 按股票分组获取数据
        codes = [s["id"] for s in services if s.get("id")]
        if not codes:
            return {}

        # ── 1. Snapshot 额外字段 (bidAskRatio, avgPrice) ──
        snapshot_data = self._fetch_snapshot_extra(codes)

        # ── 2. Capital flow (mainNetInflow, retailNetInflow) ──
        # 限频: 每 30 秒最多 30 次，只对有权限的港股请求（A 股无权限也会浪费配额）
        hk_codes = [c for c in codes if c.startswith("HK")]
        capital_data = self._fetch_capital_flow(hk_codes)

        # ── 3. 组装结果，计算 mainNetInflowPct ──
        amount_map = {s["id"]: s.get("amount", 0) for s in services}

        for code in codes:
            entry = {}

            # snapshot 字段
            snap = snapshot_data.get(code)
            if snap:
                entry["bidAskRatio"] = snap.get("bidAskRatio")
                entry["avgPrice"] = snap.get("avgPrice")

            # capital flow 字段
            cap = capital_data.get(code)
            if cap:
                entry["mainNetInflow"] = cap.get("mainNetInflow")
                entry["retailNetInflow"] = cap.get("retailNetInflow")

                # mainNetInflowPct = mainNetInflow / 当日成交额 * 100
                amount = amount_map.get(code, 0)
                main_inflow = cap.get("mainNetInflow", 0)
                if amount and main_inflow is not None:
                    entry["mainNetInflowPct"] = round(main_inflow / amount * 100, 2)

            # 只保留有实际数据的条目
            entry = {k: v for k, v in entry.items() if v is not None}
            if entry:
                result[code] = entry

        return result

    def _fetch_snapshot_extra(self, codes: list[str]) -> dict[str, dict]:
        """从 snapshot 提取 bidAskRatio 和 avgPrice"""
        from futu import RET_OK

        futu_codes = [to_futu_code(c) for c in codes]

        # 港股和 A 股分批（A 股可能没权限）
        hk = [c for c in futu_codes if c.startswith("HK.")]
        a_share = [c for c in futu_codes if not c.startswith("HK.")]

        result = {}
        for batch in [hk, a_share]:
            if not batch:
                continue
            try:
                ret, data = self._ctx.get_market_snapshot(batch)
                if ret != RET_OK:
                    continue
                for _, row in data.iterrows():
                    code = from_futu_code(row["code"])
                    bid_ask = row.get("bid_ask_ratio")
                    avg = row.get("avg_price")
                    entry = {}
                    if bid_ask and bid_ask != 0:
                        entry["bidAskRatio"] = round(float(bid_ask), 3)
                    if avg and avg != 0:
                        entry["avgPrice"] = round(float(avg), 3)
                    if entry:
                        result[code] = entry
            except Exception as e:
                logger.debug(f"snapshot batch 失败: {e}")

        return result

    def _fetch_capital_flow(self, codes: list[str]) -> dict[str, dict]:
        """获取每只股票的最新资金流向，提取主力/散户净流入"""
        from futu import RET_OK, PeriodType

        result = {}
        for code in codes:
            futu_code = to_futu_code(code)
            try:
                ret, data = self._ctx.get_capital_flow(
                    futu_code, period_type=PeriodType.INTRADAY
                )
                if ret != RET_OK:
                    continue
                if data.empty:
                    continue

                # 取最新一条
                latest = data.iloc[-1]
                super_in = float(latest.get("super_in_flow", 0) or 0)
                big_in = float(latest.get("big_in_flow", 0) or 0)
                sml_in = float(latest.get("sml_in_flow", 0) or 0)

                result[code] = {
                    "mainNetInflow": round(super_in + big_in, 2),
                    "retailNetInflow": round(sml_in, 2),
                }
            except Exception as e:
                logger.debug(f"capital_flow({code}) 失败: {e}")

        return result
