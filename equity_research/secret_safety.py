from __future__ import annotations

import re
from typing import Any, Iterable

from . import config


_QUERY_SECRET = re.compile(
    r"(?i)(apikey|api_key|api-key|token|access_token|key|secret)=([^&\s]+)"
)
_AUTHORIZATION_SECRET = re.compile(
    r"(?i)(authorization:\s*(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+"
)
_URL_CREDENTIALS = re.compile(
    r"(?i)(https?://)([^:/@\s]+):([^@\s]+)@"
)
_OPAQUE_HANDLE = re.compile(
    r"(?i)\b(?:credential|secret)[_-]?(?:reference|ref|handle)\s*[:=]\s*[^\s,;}]+"
)
_CREDENTIAL_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"
)


def redact_sensitive_text(
    value: Any,
    *,
    additional_secrets: Iterable[str] = (),
) -> str:
    """Remove configured credentials from text crossing a model/log boundary."""

    redacted = str(value or "")
    secrets = [*_configured_secrets(), *additional_secrets]
    for secret in secrets:
        normalized = str(secret or "")
        if normalized:
            redacted = redacted.replace(normalized, "[redacted]")
    redacted = _QUERY_SECRET.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)
    redacted = _AUTHORIZATION_SECRET.sub(r"\1[redacted]", redacted)
    redacted = _URL_CREDENTIALS.sub(r"\1[redacted]:[redacted]@", redacted)
    redacted = _OPAQUE_HANDLE.sub("credential_reference=[redacted]", redacted)
    return redacted


def redact_sensitive_value(value: Any) -> Any:
    """Recursively sanitize a normalized tool payload before model exposure."""

    if isinstance(value, dict):
        return {
            str(key): redact_sensitive_value(item)
            for key, item in value.items()
            if not _is_secret_field(str(key))
        }
    if isinstance(value, list):
        return [redact_sensitive_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_value(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def contains_credential_like_token(value: Any) -> bool:
    """Detect a plausible provider token without flagging normal prose.

    A bare ``"sk-"`` substring is not a useful secret test because ordinary
    phrases such as ``"risk-factor"`` contain it. Requiring a token-length,
    credential-shaped suffix keeps demo sanitization strict while making
    failures bounded and readable.
    """

    return bool(_CREDENTIAL_TOKEN.search(str(value or "")))


def _configured_secrets() -> list[str]:
    return [
        str(value)
        for name, value in vars(config).items()
        if (
            name.endswith(("_API_KEY", "_TOKEN", "_SECRET"))
            and isinstance(value, str)
            and value
        )
    ]


def _is_secret_field(field_name: str) -> bool:
    normalized = field_name.casefold().replace("-", "_")
    return normalized in {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "authorization",
        "credential_reference",
        "credential_ref",
        "credential_handle",
        "secret_ref",
        "secret_reference",
    }
