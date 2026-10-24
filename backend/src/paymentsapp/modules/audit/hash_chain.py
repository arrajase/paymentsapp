"""Hash chain for the audit log: each entry's hash covers its own fields and the previous entry's hash.

Changing, removing or reordering any entry changes every hash after it, so tampering is detectable by
recomputing the chain (verify_chain in service.py) or by comparing with an exported copy.
"""

import hashlib
import json
from datetime import datetime
from typing import Any

GENESIS_HASH = "0" * 64

# Every field that describes the event. id is excluded because the database assigns it after the hash is computed;
# ordering is protected by prev_hash instead.
HASHED_FIELDS = (
    "occurred_at",
    "actor_type",
    "actor_id",
    "action",
    "resource_type",
    "resource_id",
    "request_id",
    "ip",
    "device_id",
    "before",
    "after",
)


def _default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"cannot hash {type(value).__name__}")


def canonical(fields: dict[str, Any]) -> bytes:
    """Stable bytes for the hashed fields: sorted keys, no whitespace, timestamps in ISO 8601."""
    payload = {name: fields.get(name) for name in HASHED_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default, ensure_ascii=False).encode()


def entry_hash(prev_hash: str, fields: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + b"\n" + canonical(fields)).hexdigest()
