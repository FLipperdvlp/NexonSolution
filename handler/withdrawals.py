import json
import os

WITHDRAWALS_PATH = "withdrawals.json"


def _load_withdrawals() -> dict:
    if os.path.exists(WITHDRAWALS_PATH):
        try:
            with open(WITHDRAWALS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


_withdrawals: dict[str, dict] = _load_withdrawals()
