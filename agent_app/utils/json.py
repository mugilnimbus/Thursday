from __future__ import annotations

import json
from typing import Any


def parse_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if raw_args is None:
        return {}
    try:
        return json.loads(raw_args)
    except json.JSONDecodeError:
        return {}
