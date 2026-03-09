import copy
from typing import Any, Dict

_FINAL_STATE: Dict[str, Any] | None = None


def store_final_state(state: Dict[str, Any]) -> None:
    global _FINAL_STATE
    _FINAL_STATE = copy.deepcopy(state)


def get_enhanced_final_state() -> Dict[str, Any]:
    return copy.deepcopy(_FINAL_STATE or {})
