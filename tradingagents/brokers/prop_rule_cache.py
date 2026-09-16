"""Persistent-safe cache for researched prop-firm rule snapshots.

The cache stores only validated structured rule snapshots and provenance. It never
stores API keys, broker credentials, raw account identifiers, or order data.
Entries are keyed by provider label plus optional program/account-size hints and
fail closed on expiry, key mismatch, or source-digest corruption.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .prop_rule_research import PropFirmRuleSnapshot


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().lower().split())
    return normalized or None


@dataclass(frozen=True)
class PropFirmRuleCacheKey:
    provider_label: str
    program_hint: str | None = None
    account_size_hint: str | None = None

    def __post_init__(self) -> None:
        provider = _norm(self.provider_label)
        if not provider:
            raise ValueError("provider_label is required")
        object.__setattr__(self, "provider_label", provider)
        object.__setattr__(self, "program_hint", _norm(self.program_hint))
        object.__setattr__(self, "account_size_hint", _norm(self.account_size_hint))

    @property
    def storage_key(self) -> str:
        return json.dumps(
            [self.provider_label, self.program_hint, self.account_size_hint],
            ensure_ascii=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class PropFirmRuleCacheEntry:
    key: PropFirmRuleCacheKey
    snapshot: PropFirmRuleSnapshot
    fetched_at_ms: int
    expires_at_ms: int
    source_digest: str

    def __post_init__(self) -> None:
        if self.fetched_at_ms < 0:
            raise ValueError("fetched_at_ms must be >= 0")
        if self.expires_at_ms <= self.fetched_at_ms:
            raise ValueError("expires_at_ms must be after fetched_at_ms")
        if self.source_digest != self.snapshot.source_digest:
            raise ValueError("source_digest must match the rule snapshot")

    def public_dict(self) -> dict:
        return {
            "provider_label": self.key.provider_label,
            "program_hint": self.key.program_hint,
            "account_size_hint": self.key.account_size_hint,
            "fetched_at_ms": self.fetched_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "source_digest": self.source_digest,
            "provider_id": self.snapshot.provider_id,
            "program_name": self.snapshot.program_name,
            "account_size": self.snapshot.account_size,
            "credentials_exposed": False,
        }


class PropFirmRuleCache(Protocol):
    def get(self, *, key: PropFirmRuleCacheKey, now_ms: int) -> PropFirmRuleCacheEntry | None: ...

    def put(self, entry: PropFirmRuleCacheEntry) -> None: ...

    def invalidate(self, key: PropFirmRuleCacheKey) -> None: ...


class InMemoryPropFirmRuleCache:
    def __init__(self) -> None:
        self._entries: dict[str, PropFirmRuleCacheEntry] = {}

    def get(self, *, key: PropFirmRuleCacheKey, now_ms: int) -> PropFirmRuleCacheEntry | None:
        entry = self._entries.get(key.storage_key)
        if entry is None:
            return None
        if now_ms > entry.expires_at_ms or not _entry_matches_key(entry):
            self._entries.pop(key.storage_key, None)
            return None
        return entry

    def put(self, entry: PropFirmRuleCacheEntry) -> None:
        if not _entry_matches_key(entry):
            raise ValueError("cache entry program/account-size does not match its cache key")
        self._entries[entry.key.storage_key] = entry

    def invalidate(self, key: PropFirmRuleCacheKey) -> None:
        self._entries.pop(key.storage_key, None)


class JsonFilePropFirmRuleCache:
    """Optional file-backed cache with atomic replacement.

    The path must be explicitly configured by the caller. No default persistent
    location is invented. Corrupt, stale, or mismatched entries are ignored and
    removed on the next write.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        if not str(self._path):
            raise ValueError("cache path is required")
        self._entries: dict[str, PropFirmRuleCacheEntry] = {}
        self._load()

    def get(self, *, key: PropFirmRuleCacheKey, now_ms: int) -> PropFirmRuleCacheEntry | None:
        entry = self._entries.get(key.storage_key)
        if entry is None:
            return None
        if now_ms > entry.expires_at_ms or not _entry_matches_key(entry):
            self._entries.pop(key.storage_key, None)
            self._write()
            return None
        return entry

    def put(self, entry: PropFirmRuleCacheEntry) -> None:
        if not _entry_matches_key(entry):
            raise ValueError("cache entry program/account-size does not match its cache key")
        self._entries[entry.key.storage_key] = entry
        self._write()

    def invalidate(self, key: PropFirmRuleCacheKey) -> None:
        self._entries.pop(key.storage_key, None)
        self._write()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._entries = {}
            return
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            self._entries = {}
            return
        rows = payload.get("entries")
        if not isinstance(rows, list):
            self._entries = {}
            return
        loaded: dict[str, PropFirmRuleCacheEntry] = {}
        for row in rows:
            try:
                entry = _entry_from_dict(row)
            except (TypeError, ValueError, KeyError):
                continue
            if _entry_matches_key(entry):
                loaded[entry.key.storage_key] = entry
        self._entries = loaded

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "entries": [_entry_to_dict(item) for item in self._entries.values()],
        }
        temp = self._path.with_name(self._path.name + ".tmp")
        temp.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        os.replace(temp, self._path)


def _entry_matches_key(entry: PropFirmRuleCacheEntry) -> bool:
    snapshot_program = _norm(entry.snapshot.program_name)
    snapshot_size = _norm(entry.snapshot.account_size)
    if entry.key.program_hint is not None and snapshot_program != entry.key.program_hint:
        return False
    if entry.key.account_size_hint is not None and snapshot_size != entry.key.account_size_hint:
        return False
    return entry.source_digest == entry.snapshot.source_digest


def _entry_to_dict(entry: PropFirmRuleCacheEntry) -> dict:
    return {
        "key": asdict(entry.key),
        "snapshot": entry.snapshot.public_dict(include_digest=False),
        "fetched_at_ms": entry.fetched_at_ms,
        "expires_at_ms": entry.expires_at_ms,
        "source_digest": entry.source_digest,
    }


def _entry_from_dict(payload: dict) -> PropFirmRuleCacheEntry:
    key_data = payload["key"]
    snap_data = dict(payload["snapshot"])
    snap_data.pop("research_source_policy", None)
    snap_data.pop("source_digest", None)
    snapshot = PropFirmRuleSnapshot(**snap_data)
    return PropFirmRuleCacheEntry(
        key=PropFirmRuleCacheKey(**key_data),
        snapshot=snapshot,
        fetched_at_ms=int(payload["fetched_at_ms"]),
        expires_at_ms=int(payload["expires_at_ms"]),
        source_digest=str(payload["source_digest"]),
    )
