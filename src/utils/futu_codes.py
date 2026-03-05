"""Futu 代码映射工具 — 本项目代码格式 ↔ Futu OpenAPI 格式。

统一代码映射逻辑，消除 futu_enricher / l2_strategy_engine / futu_quote_demo 的重复。

映射规则:
    HK09988  → HK.09988
    688676   → SH.688676
    002848   → SZ.002848
    000001   → SZ.000001
"""


def to_futu_code(code: str) -> str:
    """本项目代码 → Futu 格式 (e.g. HK09988 → HK.09988)"""
    if code.startswith("HK"):
        return f"HK.{code[2:]}"
    first = code[0]
    if first in ("6", "5"):
        return f"SH.{code}"
    if first in ("0", "1", "2", "3"):
        return f"SZ.{code}"
    return f"SH.{code}"


def from_futu_code(futu_code: str) -> str:
    """Futu 格式 → 本项目代码 (e.g. HK.09988 → HK09988)"""
    market, num = futu_code.split(".", 1)
    if market == "HK":
        return f"HK{num}"
    return num
