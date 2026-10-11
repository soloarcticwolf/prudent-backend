"""Deterministic hashing + UTC time helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(obj: Any) -> str:
    if isinstance(obj, str):
        payload = obj.encode("utf-8")
    else:
        payload = canonical_json(obj).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
