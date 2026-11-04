"""Canonical JSON hashing shared by approvals, tools and the audit store."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_payload(payload: Any) -> Any:
    """Convert a Pydantic model to JSON-native data before canonicalisation."""

    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return payload


def canonical_json(payload: Any) -> str:
    return json.dumps(
        canonical_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def canonical_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
