from __future__ import annotations

from typing import Any, Mapping


def sanitize_text(value: str | None) -> str | None:
    """
    Remove characters that PostgreSQL TEXT cannot store and coerce to valid UTF-8.

    - Strips NUL (\x00) which Postgres rejects for TEXT
    - Re-encodes with errors="replace" to ensure valid UTF-8 sequences
    """
    if value is None:
        return None
    # Remove NUL bytes explicitly
    without_nuls = value.replace("\x00", "")
    # Force valid UTF-8 by replacing invalid sequences
    coerced = without_nuls.encode("utf-8", "replace").decode("utf-8")
    return coerced


def sanitize_json(obj: Any) -> Any:
    """
    Recursively sanitize a JSON-serializable structure so that all contained
    strings are safe to store in PostgreSQL JSONB.
    """
    if obj is None:
        return None

    if isinstance(obj, str):
        return sanitize_text(obj)

    if isinstance(obj, Mapping):
        return {sanitize_json(k): sanitize_json(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        # Preserve list/tuple semantics; sets will be converted to lists
        if isinstance(obj, tuple):
            return tuple(sanitize_json(v) for v in obj)
        return [sanitize_json(v) for v in obj]

    # Leave numbers, booleans, etc. unchanged
    return obj
