from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable, Mapping


@dataclass(frozen=True)
class CacheIsolationKey:
    """The policy boundary that cached evidence is permitted to cross."""

    source_policy_fingerprint: str = "default"
    entitlement_fingerprint: str = "default"
    data_cutoff: str = ""
    parser_version: str = "external-evidence-v2"
    workspace_scope: str = "local"
    isolation_version: str = "cache-isolation-v1"

    def __post_init__(self) -> None:
        if not self.parser_version.strip():
            raise ValueError("A parser version is required for evidence caching.")
        if not self.workspace_scope.strip():
            raise ValueError("A workspace scope is required for evidence caching.")


def build_cache_isolation_key(
    *,
    enabled_source_families: Iterable[str] = (),
    entitlement_rules: Mapping[str, object] | None = None,
    data_cutoff: str = "",
    parser_version: str = "external-evidence-v2",
    workspace_scope: str = "local",
) -> CacheIsolationKey:
    """Build a stable cache scope without including credentials or tokens."""

    source_policy = sorted({
        str(item).strip().casefold()
        for item in enabled_source_families
        if str(item).strip()
    })
    entitlements = {
        str(key).strip().casefold(): str(value).strip().casefold()
        for key, value in sorted((entitlement_rules or {}).items())
        if str(key).strip()
    }
    return CacheIsolationKey(
        source_policy_fingerprint=_fingerprint({
            "enabled_source_families": source_policy,
        }),
        entitlement_fingerprint=_fingerprint({
            "entitlement_rules": entitlements,
        }),
        data_cutoff=str(data_cutoff or ""),
        parser_version=parser_version,
        workspace_scope=workspace_scope,
    )


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
