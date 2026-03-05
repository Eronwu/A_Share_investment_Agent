"""Futu 模拟盘交易适配器 — 封装 OpenSecTradeContext。

Lazy connect + graceful degradation，与 FutuL2Enricher 同模式。
支持 HK + A 股双市场，自动发现模拟账户并按代码前缀路由。

设计原则:
  - 连接失败 → 返回失败结果，不抛异常
  - 15 orders/30s 滑窗限频
  - A 股 T+1: sell() 本地拦截当日买入的卖出
  - 所有 API 调用捕获异常，保证调用方不因 Futu 故障崩溃
"""

import logging
import socket
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.utils.futu_codes import to_futu_code, from_futu_code

logger = logging.getLogger("l2_daemon.futu_trade")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
RECONNECT_COOLDOWN = 60

# Futu 下单频率限制: 15 orders / 30s
ORDER_RATE_WINDOW = 30
ORDER_RATE_LIMIT = 15


# ── Data classes ──────────────────────────────────────────


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    error_msg: str = ""


@dataclass
class FutuPosition:
    code: str          # 本项目格式 (HK09988, 002848)
    quantity: int = 0
    avg_price: float = 0.0
    market_val: float = 0.0
    unrealized_pnl: float = 0.0
    today_pnl: float = 0.0
    today_buy_qty: int = 0   # 今日买入数量 (A 股 T+1 判断用)
    name: str = ""           # 股票名称 (from Futu stock_name)


@dataclass
class FutuFunds:
    total_assets: float = 0.0
    cash: float = 0.0
    market_val: float = 0.0
    available: float = 0.0


@dataclass
class OrderUpdate:
    order_id: str
    code: str           # 本项目格式
    status: str         # FILLED / CANCELLED / PARTIAL / PENDING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    side: str = ""      # BUY / SELL


# ── Adapter ───────────────────────────────────────────────


class FutuTradeAdapter:
    """Futu 模拟盘交易适配器。

    Usage:
        adapter = FutuTradeAdapter()
        if adapter.connect():
            result = adapter.buy("HK09988", 80.0, 100)
            positions = adapter.get_positions()
    """

    def __init__(self, host: str = OPEND_HOST, port: int = OPEND_PORT):
        self._host = host
        self._port = port
        self._ctx = None
        self._last_fail_time: float = 0
        self._hk_acc_id: int | None = None
        self._a_acc_id: int | None = None
        self._order_timestamps: deque = deque()  # rate limiting

    # ── Connection ─────────────────────────────────────────

    def _is_port_open(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((self._host, self._port)) == 0
        sock.close()
        return result

    def connect(self) -> bool:
        """连接 OpenD 并发现模拟账户。成功返回 True。"""
        if self._ctx is not None:
            return True

        if time.time() - self._last_fail_time < RECONNECT_COOLDOWN:
            return False

        if not self._is_port_open():
            self._last_fail_time = time.time()
            logger.debug("OpenD 未运行，Futu 交易不可用")
            return False

        try:
            from futu import OpenSecTradeContext, TrdEnv, TrdMarket
            self._ctx = OpenSecTradeContext(
                host=self._host, port=self._port,
                filter_trdmarket=TrdMarket.NONE,
                security_firm=None,
            )
            self._discover_accounts()
            if not self._hk_acc_id and not self._a_acc_id:
                logger.warning("未发现任何模拟交易账户")
                self.close()
                self._last_fail_time = time.time()
                return False

            markets = []
            if self._hk_acc_id:
                markets.append(f"HK(acc={self._hk_acc_id})")
            if self._a_acc_id:
                markets.append(f"A-share(acc={self._a_acc_id})")
            logger.info(f"Futu trade context connected: {', '.join(markets)}")
            return True
        except Exception as e:
            self._last_fail_time = time.time()
            logger.warning(f"Futu trade 连接失败: {e}")
            self._ctx = None
            return False

    def _discover_accounts(self):
        """从 get_acc_list 发现 HK 和 A 股模拟账户。

        Futu SDK 返回字段为字符串（非枚举）:
          trd_env: "SIMULATE" / "REAL"
          trdmarket_auth: ["HK"] / ["SH", "SZ"]
          sim_acc_type: "STOCK" / "OPTION" / "N/A"
        """
        from futu import RET_OK

        ret, data = self._ctx.get_acc_list()
        if ret != RET_OK:
            logger.warning(f"get_acc_list failed: {data}")
            return

        for _, row in data.iterrows():
            trd_env = str(row.get("trd_env", ""))
            if "SIMULATE" not in trd_env:
                continue

            # 跳过期权模拟账户
            sim_type = str(row.get("sim_acc_type", ""))
            if "OPTION" in sim_type:
                continue

            acc_id = int(row["acc_id"])
            market_auth = row.get("trdmarket_auth", [])
            if isinstance(market_auth, str):
                market_auth = [market_auth]

            # 字符串匹配: "HK", "SH", "SZ", "US" etc.
            market_strs = [str(m) for m in market_auth]

            if "HK" in market_strs and self._hk_acc_id is None:
                self._hk_acc_id = acc_id
                logger.debug(f"Found HK sim account: {acc_id}")
            if any(m in ("SH", "SZ") for m in market_strs) and self._a_acc_id is None:
                self._a_acc_id = acc_id
                logger.debug(f"Found A-share sim account: {acc_id}")

    def _select_account(self, code: str) -> int | None:
        """根据代码前缀选择模拟账户 ID。"""
        if code.startswith("HK"):
            return self._hk_acc_id
        return self._a_acc_id

    def _get_trd_env(self):
        from futu import TrdEnv
        return TrdEnv.SIMULATE

    # ── Rate limiting ──────────────────────────────────────

    def _enforce_rate_limit(self):
        """15 orders/30s 滑窗限频。超限时阻塞等待。"""
        now = time.time()
        # 清理过期时间戳
        while self._order_timestamps and self._order_timestamps[0] < now - ORDER_RATE_WINDOW:
            self._order_timestamps.popleft()

        if len(self._order_timestamps) >= ORDER_RATE_LIMIT:
            wait = self._order_timestamps[0] + ORDER_RATE_WINDOW - now + 0.1
            if wait > 0:
                logger.info(f"Rate limit reached, waiting {wait:.1f}s")
                time.sleep(wait)

        self._order_timestamps.append(time.time())

    # ── Order placement ────────────────────────────────────

    def buy(self, code: str, price: float, quantity: int,
            order_type: str = "NORMAL") -> OrderResult:
        """下买单。返回 OrderResult。"""
        return self._place_order(code, price, quantity, "BUY", order_type)

    def sell(self, code: str, price: float, quantity: int,
             order_type: str = "NORMAL") -> OrderResult:
        """下卖单。A 股 T+1 本地拦截。"""
        # A 股 T+1 guard: 当日买入的不能卖
        if not code.startswith("HK"):
            today_buy = self._get_today_buy_qty(code)
            if today_buy > 0:
                sellable = quantity - today_buy
                if sellable <= 0:
                    return OrderResult(
                        success=False,
                        error_msg=f"A 股 T+1: {code} 今日买入 {today_buy} 股，"
                                  f"可卖 {max(0, sellable)} 股"
                    )
                if sellable < quantity:
                    logger.info(
                        f"A 股 T+1: {code} 今日买入 {today_buy}，"
                        f"调整卖出量 {quantity} → {sellable}"
                    )
                    quantity = sellable

        return self._place_order(code, price, quantity, "SELL", order_type)

    def _place_order(self, code: str, price: float, quantity: int,
                     side: str, order_type: str) -> OrderResult:
        """Internal order placement."""
        if not self._ctx:
            return OrderResult(success=False, error_msg="Not connected")

        acc_id = self._select_account(code)
        if acc_id is None:
            return OrderResult(success=False, error_msg=f"No account for {code}")

        self._enforce_rate_limit()

        try:
            from futu import RET_OK, TrdSide, OrderType

            futu_code = to_futu_code(code)
            futu_side = TrdSide.BUY if side == "BUY" else TrdSide.SELL

            # A 股模拟盘只支持限价单
            if not code.startswith("HK") and order_type == "MARKET":
                order_type = "NORMAL"

            futu_order_type = OrderType.NORMAL
            if order_type == "MARKET":
                futu_order_type = OrderType.MARKET

            ret, data = self._ctx.place_order(
                price=price,
                qty=quantity,
                code=futu_code,
                trd_side=futu_side,
                order_type=futu_order_type,
                trd_env=self._get_trd_env(),
                acc_id=acc_id,
            )

            if ret == RET_OK:
                if data is None or data.empty:
                    return OrderResult(success=False, error_msg="Empty response from Futu")
                order_id = str(data.iloc[0]["order_id"])
                logger.info(
                    f"Order placed: {side} {code} {quantity}@{price:.2f} "
                    f"→ order_id={order_id}"
                )
                return OrderResult(success=True, order_id=order_id)
            else:
                logger.warning(f"Order failed: {side} {code} → {data}")
                return OrderResult(success=False, error_msg=str(data))

        except Exception as e:
            logger.error(f"Order exception: {side} {code} → {e}")
            return OrderResult(success=False, error_msg=str(e))

    def cancel_order(self, order_id: str, code: str = "") -> bool:
        """撤单。"""
        if not self._ctx:
            return False
        try:
            from futu import RET_OK
            acc_id = self._select_account(code) if code else self._hk_acc_id
            ret, data = self._ctx.modify_order(
                modify_order_op="CANCEL",
                order_id=order_id,
                qty=0, price=0,
                trd_env=self._get_trd_env(),
                acc_id=acc_id,
            )
            if ret == RET_OK:
                logger.info(f"Order cancelled: {order_id}")
                return True
            logger.warning(f"Cancel failed: {order_id} → {data}")
            return False
        except Exception as e:
            logger.error(f"Cancel exception: {order_id} → {e}")
            return False

    # ── Queries ────────────────────────────────────────────

    def get_positions(self, market: str | None = None) -> dict[str, FutuPosition]:
        """获取持仓。返回 {本项目代码: FutuPosition}。"""
        if not self._ctx:
            return {}

        result = {}
        acc_ids = []
        if market == "HK" and self._hk_acc_id:
            acc_ids = [self._hk_acc_id]
        elif market == "A" and self._a_acc_id:
            acc_ids = [self._a_acc_id]
        else:
            if self._hk_acc_id:
                acc_ids.append(self._hk_acc_id)
            if self._a_acc_id:
                acc_ids.append(self._a_acc_id)

        def _safe_float(val, default=0.0):
            try:
                return float(val)
            except (ValueError, TypeError):
                return default

        def _safe_int(val, default=0):
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        try:
            from futu import RET_OK
            for acc_id in acc_ids:
                ret, data = self._ctx.position_list_query(
                    trd_env=self._get_trd_env(),
                    acc_id=acc_id,
                )
                if ret != RET_OK:
                    logger.warning(f"position_list_query failed (acc={acc_id}): {data}")
                    continue

                for _, row in data.iterrows():
                    qty = _safe_int(row.get("qty", 0))
                    if qty <= 0:
                        continue
                    code = from_futu_code(row["code"])
                    result[code] = FutuPosition(
                        code=code,
                        quantity=qty,
                        avg_price=_safe_float(row.get("cost_price", 0)),
                        market_val=_safe_float(row.get("market_val", 0)),
                        unrealized_pnl=_safe_float(row.get("pl_val", 0)),
                        today_pnl=_safe_float(row.get("today_pl_val", 0)),
                        today_buy_qty=_safe_int(row.get("today_buy_qty", 0)),
                        name=str(row.get("stock_name", "")),
                    )
        except Exception as e:
            logger.error(f"get_positions error: {e}")

        return result

    def get_funds(self, market: str | None = None) -> FutuFunds:
        """获取账户资金。"""
        if not self._ctx:
            return FutuFunds()

        # 默认查 HK 账户，如果指定 A 则查 A 股
        acc_id = self._a_acc_id if market == "A" else self._hk_acc_id
        if acc_id is None:
            return FutuFunds()

        try:
            from futu import RET_OK
            ret, data = self._ctx.accinfo_query(
                trd_env=self._get_trd_env(),
                acc_id=acc_id,
            )
            if ret != RET_OK:
                logger.warning(f"accinfo_query failed: {data}")
                return FutuFunds()

            if data is None or data.empty:
                logger.warning("accinfo_query returned no rows")
                return FutuFunds()

            row = data.iloc[0]
            return FutuFunds(
                total_assets=float(row.get("total_assets", 0)),
                cash=float(row.get("cash", 0)),
                market_val=float(row.get("market_val", 0)),
                available=float(row.get("avl_withdrawal_cash", 0)),
            )
        except Exception as e:
            logger.error(f"get_funds error: {e}")
            return FutuFunds()

    def check_pending_orders(self, code: str = "") -> list[OrderUpdate]:
        """查询未完成订单状态。"""
        if not self._ctx:
            return []

        result = []
        acc_ids = []
        if self._hk_acc_id:
            acc_ids.append(self._hk_acc_id)
        if self._a_acc_id:
            acc_ids.append(self._a_acc_id)

        try:
            from futu import RET_OK, OrderStatus
            for acc_id in acc_ids:
                ret, data = self._ctx.order_list_query(
                    trd_env=self._get_trd_env(),
                    acc_id=acc_id,
                    status_filter_list=[
                        OrderStatus.SUBMITTED,
                        OrderStatus.FILLED_PART,
                    ],
                )
                if ret != RET_OK:
                    continue

                for _, row in data.iterrows():
                    oid = str(row["order_id"])
                    c = from_futu_code(row["code"])
                    if code and c != code:
                        continue

                    status_map = {
                        OrderStatus.FILLED_ALL: "FILLED",
                        OrderStatus.FILLED_PART: "PARTIAL",
                        OrderStatus.CANCELLED_ALL: "CANCELLED",
                        OrderStatus.SUBMITTED: "PENDING",
                    }
                    raw_status = row.get("order_status", "")
                    status = status_map.get(raw_status, str(raw_status))

                    result.append(OrderUpdate(
                        order_id=oid,
                        code=c,
                        status=status,
                        filled_qty=int(row.get("dealt_qty", 0)),
                        avg_fill_price=float(row.get("dealt_avg_price", 0)),
                        side="BUY" if "BUY" in str(row.get("trd_side", "")) else "SELL",
                    ))
        except Exception as e:
            logger.error(f"check_pending_orders error: {e}")

        return result

    def get_today_orders(self, code: str = "") -> list[OrderUpdate]:
        """查询今日所有订单（含已完成）。"""
        if not self._ctx:
            return []

        result = []
        acc_ids = []
        if self._hk_acc_id:
            acc_ids.append(self._hk_acc_id)
        if self._a_acc_id:
            acc_ids.append(self._a_acc_id)

        try:
            from futu import RET_OK
            for acc_id in acc_ids:
                ret, data = self._ctx.order_list_query(
                    trd_env=self._get_trd_env(),
                    acc_id=acc_id,
                )
                if ret != RET_OK:
                    continue

                for _, row in data.iterrows():
                    oid = str(row["order_id"])
                    c = from_futu_code(row["code"])
                    if code and c != code:
                        continue
                    result.append(OrderUpdate(
                        order_id=oid,
                        code=c,
                        status=str(row.get("order_status", "")),
                        filled_qty=int(row.get("dealt_qty", 0)),
                        avg_fill_price=float(row.get("dealt_avg_price", 0)),
                        side="BUY" if "BUY" in str(row.get("trd_side", "")) else "SELL",
                    ))
        except Exception as e:
            logger.error(f"get_today_orders error: {e}")

        return result

    def _get_today_buy_qty(self, code: str) -> int:
        """获取某股票今日已买入数量 (用于 A 股 T+1 判断)。"""
        positions = self.get_positions()
        fp = positions.get(code)
        if fp:
            return fp.today_buy_qty
        return 0

    # ── Lifecycle ──────────────────────────────────────────

    def close(self):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None
            self._hk_acc_id = None
            self._a_acc_id = None

    @property
    def connected(self) -> bool:
        return self._ctx is not None

    @property
    def hk_acc_id(self) -> int | None:
        return self._hk_acc_id

    @property
    def a_acc_id(self) -> int | None:
        return self._a_acc_id
