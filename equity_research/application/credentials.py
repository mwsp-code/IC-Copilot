from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
import threading
import uuid


class CredentialLeaseError(RuntimeError):
    """Raised when an ephemeral credential cannot be used safely."""


@dataclass(frozen=True)
class CredentialLeaseReference:
    lease_id: str
    provider_id: str
    scope: str
    expires_at: str
    source_label: str = "Available this session · not saved"


class EphemeralCredentialBroker(Protocol):
    def issue(
        self,
        provider_id: str,
        secret: str,
        *,
        scope: str,
        ttl_seconds: int = 14_400,
    ) -> CredentialLeaseReference: ...

    def resolve(
        self,
        reference: CredentialLeaseReference,
        *,
        provider_id: str,
        scope: str,
    ) -> str: ...

    def revoke(self, lease_id: str) -> None: ...


@dataclass
class _CredentialLease:
    reference: CredentialLeaseReference
    secret: str


class InMemoryEphemeralCredentialBroker:
    """Process-local credential leases; secrets never enter commands or artifacts."""

    def __init__(self) -> None:
        self._leases: dict[str, _CredentialLease] = {}
        self._lock = threading.RLock()

    def issue(
        self,
        provider_id: str,
        secret: str,
        *,
        scope: str,
        ttl_seconds: int = 14_400,
    ) -> CredentialLeaseReference:
        clean_secret = str(secret or "").strip()
        if not clean_secret:
            raise CredentialLeaseError("A non-empty credential is required.")
        if ttl_seconds <= 0:
            raise CredentialLeaseError("Credential lease TTL must be positive.")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        reference = CredentialLeaseReference(
            lease_id=str(uuid.uuid4()),
            provider_id=str(provider_id or "").strip(),
            scope=str(scope or "").strip(),
            expires_at=expires_at.isoformat(timespec="seconds"),
        )
        with self._lock:
            self._leases[reference.lease_id] = _CredentialLease(
                reference=reference,
                secret=clean_secret,
            )
        return reference

    def resolve(
        self,
        reference: CredentialLeaseReference,
        *,
        provider_id: str,
        scope: str,
    ) -> str:
        with self._lock:
            lease = self._leases.get(reference.lease_id)
        if lease is None:
            raise CredentialLeaseError("Credential lease is unavailable or revoked.")
        if lease.reference.provider_id != provider_id or lease.reference.scope != scope:
            raise CredentialLeaseError("Credential lease scope does not match this job.")
        if _parse_time(lease.reference.expires_at) <= datetime.now(timezone.utc):
            self.revoke(reference.lease_id)
            raise CredentialLeaseError("Credential lease expired; reconnect to resume.")
        return lease.secret

    def revoke(self, lease_id: str) -> None:
        with self._lock:
            self._leases.pop(str(lease_id or ""), None)

    def purge_expired(self) -> int:
        now = datetime.now(timezone.utc)
        with self._lock:
            expired = [
                lease_id
                for lease_id, lease in self._leases.items()
                if _parse_time(lease.reference.expires_at) <= now
            ]
            for lease_id in expired:
                self._leases.pop(lease_id, None)
        return len(expired)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
