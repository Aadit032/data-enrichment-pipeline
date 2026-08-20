"""A simple JSON-file cache.

Every expensive step (Exa searches, page fetches, LLM calls) is keyed by a
content hash of its *inputs*. Rerunning the pipeline therefore skips work that
has already been done, which also makes the pipeline safely resumable after an
interruption.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

SCHEMA_VERSION = "1"


def payload_key(payload: Any) -> str:
    """Deterministic content-hash for an arbitrary JSON-serialisable payload."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(f"{SCHEMA_VERSION}:{blob}".encode()).hexdigest()


def _serializable(value: Any) -> Any:
    """Recursively convert Pydantic models to plain JSON-serialisable data."""
    if isinstance(value, BaseModel):
        return _serializable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {k: _serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(v) for v in value]
    return value


class JsonCache:
    """Thread-safe-ish file cache organised as ``root/<namespace>/<key>.json``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, namespace: str, key: str) -> Path:
        directory = self.root / namespace
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{key}.json"

    def get(self, namespace: str, key: str) -> Any | None:
        path = self._path(namespace, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        path = self._path(namespace, key)
        path.write_text(json.dumps(value, default=str, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_or_compute(
        self,
        namespace: str,
        payload: Any,
        compute: Callable[[], Any],
        model: type[T] | None = None,
    ) -> T | Any:
        """Return cached value for ``payload`` or compute, store and return it.

        When ``model`` is given, both the cached payload and the freshly
        computed value are validated/coerced through that Pydantic model.
        """
        key = payload_key(payload)
        cached = self.get(namespace, key)
        if cached is not None:
            return model.model_validate(cached) if model else cached
        value = compute()
        serializable = _serializable(value)
        self.set(namespace, key, serializable)
        return value if model is None else value if isinstance(value, model) else model.model_validate(serializable)
