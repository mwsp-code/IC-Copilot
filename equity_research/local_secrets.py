from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import dataclass
from ctypes import wintypes
from datetime import date
from pathlib import Path
from socket import timeout as SocketTimeout
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SERVICE_NAME = "equity-research-radar"
LLM_PROFILE_SECRET_PREFIX = "LLM_PROFILE_API_KEY::"
MANAGED_SECRET_KEYS = {
    "ALPHAVANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "FRED_API_KEY",
    "BEA_API_KEY",
    "CENSUS_API_KEY",
    "WISBURG_API_KEY",
    "TIINGO_API_KEY",
    "EODHD_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "QWEN_API_KEY",
    "KIMI_API_KEY",
    "DEEPSEEK_API_KEY",
    "SEC_USER_AGENT",
}
VALIDATION_KEYS = {
    "ALPHAVANTAGE_API_KEY",
    "FINNHUB_API_KEY",
    "FMP_API_KEY",
    "FRED_API_KEY",
    "BEA_API_KEY",
    "CENSUS_API_KEY",
    "TIINGO_API_KEY",
    "EODHD_API_KEY",
}
SECRET_LABELS = {
    "ALPHAVANTAGE_API_KEY": "Alpha Vantage",
    "FINNHUB_API_KEY": "Finnhub",
    "FMP_API_KEY": "FMP",
    "FRED_API_KEY": "FRED",
    "BEA_API_KEY": "BEA",
    "CENSUS_API_KEY": "Census",
    "WISBURG_API_KEY": "Wisburg",
    "TIINGO_API_KEY": "Tiingo",
    "EODHD_API_KEY": "EODHD",
    "OPENAI_API_KEY": "OpenAI API",
    "ANTHROPIC_API_KEY": "Anthropic API",
    "QWEN_API_KEY": "Qwen API",
    "KIMI_API_KEY": "Kimi API",
    "DEEPSEEK_API_KEY": "DeepSeek API",
    "SEC_USER_AGENT": "SEC user agent",
}
_AUTO_BACKEND = object()


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None:
        ...

    def set_password(self, service_name: str, username: str, password: str) -> None:
        ...

    def delete_password(self, service_name: str, username: str) -> None:
        ...


@dataclass
class SecretStatus:
    key: str
    label: str
    configured: bool
    source: str
    backend_available: bool
    backend: str
    message: str = ""


@dataclass
class ValidationResult:
    key: str
    label: str
    status: str
    message: str


class LocalSecretsManager:
    def __init__(
        self,
        backend: KeyringBackend | None | object = _AUTO_BACKEND,
        service_name: str = SERVICE_NAME,
    ) -> None:
        self.service_name = service_name
        self.backend = _default_keyring_backend() if backend is _AUTO_BACKEND else backend
        self.backend_available = self.backend is not None
        self.backend_name = getattr(self.backend, "backend_name", "os_keychain") if self.backend else "unavailable"

    def get(self, key: str) -> str | None:
        _require_allowed_key(key)
        if not self.backend:
            return None
        try:
            return self.backend.get_password(self.service_name, key) or None
        except Exception:
            return None

    def set(self, key: str, value: str) -> None:
        _require_allowed_key(key)
        if not self.backend:
            raise RuntimeError("OS keychain backend is unavailable.")
        self.backend.set_password(self.service_name, key, value)
        os.environ[key] = value

    def delete(self, key: str) -> None:
        _require_allowed_key(key)
        if not self.backend:
            return
        try:
            self.backend.delete_password(self.service_name, key)
        except Exception:
            pass
        os.environ.pop(key, None)

    def delete_many(self, keys: list[str] | None = None) -> list[str]:
        deleted = []
        for key in keys or sorted(MANAGED_SECRET_KEYS):
            self.delete(key)
            deleted.append(key)
        return deleted

    def status(self, system_env_keys: set[str] | None = None) -> list[SecretStatus]:
        system_env_keys = system_env_keys or set()
        rows: list[SecretStatus] = []
        for key in sorted(MANAGED_SECRET_KEYS):
            env_value = os.getenv(key, "")
            keychain_value = None if key in system_env_keys else self.get(key)
            if key in system_env_keys and env_value:
                source = "environment"
                configured = True
            elif keychain_value:
                source = "os_keychain"
                configured = True
            elif env_value:
                source = "local_env"
                configured = True
            else:
                source = "missing"
                configured = False
            rows.append(SecretStatus(
                key=key,
                label=SECRET_LABELS.get(key, key),
                configured=configured,
                source=source,
                backend_available=self.backend_available,
                backend=self.backend_name,
                message="" if self.backend_available else "OS keychain backend is unavailable.",
            ))
        return rows

    def redacted_status(self, system_env_keys: set[str] | None = None) -> list[dict]:
        return [
            {
                "key": item.key,
                "label": item.label,
                "configured": item.configured,
                "source": item.source,
                "backend_available": item.backend_available,
                "backend": item.backend,
                "message": item.message,
            }
            for item in self.status(system_env_keys)
        ]

    def load_into_environment(self, system_env_keys: set[str] | None = None) -> dict[str, str]:
        loaded: dict[str, str] = {}
        system_env_keys = system_env_keys or set()
        if not self.backend:
            return loaded
        for key in sorted(MANAGED_SECRET_KEYS):
            if key in system_env_keys:
                continue
            value = self.get(key)
            if value:
                os.environ[key] = value
                loaded[key] = "os_keychain"
        return loaded


class DpapiFileBackend:
    backend_name = "windows_dpapi"

    def __init__(
        self,
        vault_path: Path | str | None = None,
        *,
        protect=None,
        unprotect=None,
    ) -> None:
        self.vault_path = Path(vault_path or os.getenv("LOCAL_SECRET_VAULT") or Path("data") / "local_secrets.dpapi.json")
        self._protect = protect or _dpapi_protect
        self._unprotect = unprotect or _dpapi_unprotect

    def get_password(self, service_name: str, username: str) -> str | None:
        encoded = self._read_vault().get(service_name, {}).get(username)
        if not encoded:
            return None
        protected = base64.b64decode(encoded.encode("ascii"))
        return self._unprotect(protected).decode("utf-8")

    def set_password(self, service_name: str, username: str, password: str) -> None:
        vault = self._read_vault()
        service = vault.setdefault(service_name, {})
        protected = self._protect(password.encode("utf-8"))
        service[username] = base64.b64encode(protected).decode("ascii")
        self._write_vault(vault)

    def delete_password(self, service_name: str, username: str) -> None:
        vault = self._read_vault()
        service = vault.get(service_name, {})
        service.pop(username, None)
        if not service:
            vault.pop(service_name, None)
        self._write_vault(vault)

    def _read_vault(self) -> dict:
        if not self.vault_path.exists():
            return {}
        try:
            return json.loads(self.vault_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_vault(self, vault: dict) -> None:
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.vault_path.with_suffix(self.vault_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(vault, sort_keys=True), encoding="utf-8")
        os.replace(tmp_path, self.vault_path)


def validate_provider_keys(
    keys: dict[str, str],
    *,
    fetch_json=None,
    timeout_seconds: int = 12,
) -> list[ValidationResult]:
    fetcher = fetch_json or _fetch_json
    results: list[ValidationResult] = []
    for key, value in keys.items():
        if key not in VALIDATION_KEYS:
            continue
        clean_value = (value or "").strip()
        if not clean_value:
            results.append(_validation(key, "not_configured", "No key was supplied."))
            continue
        try:
            result = _validate_one(key, clean_value, fetcher, timeout_seconds)
        except TimeoutError:
            result = _validation(key, "timeout", "Provider validation timed out.")
        except (URLError, OSError, SocketTimeout) as exc:
            failure_class, message = _network_error_message(exc)
            result = _validation(key, "network_error", _redact(f"{failure_class}: {message}", clean_value))
        except json.JSONDecodeError:
            result = _validation(key, "malformed_response", "Provider returned malformed JSON.")
        except Exception as exc:
            failure_class, message = _network_error_message(exc)
            result = _validation(key, "network_error", _redact(f"{failure_class}: {message}", clean_value))
        results.append(result)
    return results


def save_validated_keys(
    manager: LocalSecretsManager,
    keys: dict[str, str],
    validation_results: list[ValidationResult],
) -> dict:
    valid_keys = {item.key for item in validation_results if item.status == "valid"}
    saved = []
    skipped = []
    for key, value in keys.items():
        if key not in MANAGED_SECRET_KEYS:
            continue
        clean_value = (value or "").strip()
        if not clean_value:
            continue
        if key in VALIDATION_KEYS and key not in valid_keys:
            skipped.append(key)
            continue
        manager.set(key, clean_value)
        saved.append(key)
    return {"saved": saved, "skipped": skipped}


def _validate_one(key: str, value: str, fetcher, timeout_seconds: int) -> ValidationResult:
    if key == "ALPHAVANTAGE_API_KEY":
        payload = fetcher(
            "https://www.alphavantage.co/query?"
            + urlencode({"function": "OVERVIEW", "symbol": "AAPL", "apikey": value}),
            timeout_seconds,
        )
        if "Note" in payload or "Information" in payload:
            return _validation(key, "rate_limited", _redact(str(payload.get("Note") or payload.get("Information")), value))
        if "Error Message" in payload:
            return _validation(key, "invalid_key", "Alpha Vantage rejected the request.")
        return _validation(key, "valid", "Alpha Vantage key returned a usable response.")
    if key == "FINNHUB_API_KEY":
        payload = fetcher(
            "https://finnhub.io/api/v1/stock/recommendation?"
            + urlencode({"symbol": "AAPL", "token": value}),
            timeout_seconds,
        )
        if isinstance(payload, dict) and payload.get("error"):
            return _validation(key, "invalid_key", _redact(str(payload.get("error")), value))
        if not isinstance(payload, list):
            return _validation(key, "malformed_response", "Finnhub returned an unexpected response shape.")
        return _validation(key, "valid", "Finnhub key returned a usable response.")
    if key == "FMP_API_KEY":
        payload = fetcher(
            "https://financialmodelingprep.com/stable/price-target-consensus?"
            + urlencode({"symbol": "AAPL", "apikey": value}),
            timeout_seconds,
        )
        if isinstance(payload, dict) and payload.get("error"):
            message = _redact(str(payload.get("error")), value)
            if "403" in message or "permission" in message.lower() or "not available" in message.lower():
                return _validation(key, "entitlement_error", message)
            if "limit" in message.lower() or "rate" in message.lower():
                return _validation(key, "rate_limited", message)
            return _validation(key, "invalid_key", message)
        if not isinstance(payload, list):
            return _validation(key, "malformed_response", "FMP returned an unexpected response shape.")
        return _validation(key, "valid", "FMP key returned a usable price-target consensus response.")
    if key == "FRED_API_KEY":
        payload = fetcher(
            "https://api.stlouisfed.org/fred/series/observations?"
            + urlencode({"series_id": "DGS10", "api_key": value, "file_type": "json", "limit": "1"}),
            timeout_seconds,
        )
        if payload.get("error_code"):
            return _validation(key, "invalid_key", _redact(str(payload.get("error_message") or "FRED rejected the key."), value))
        if "observations" not in payload:
            return _validation(key, "malformed_response", "FRED returned an unexpected response shape.")
        return _validation(key, "valid", "FRED key returned a usable response.")
    if key == "BEA_API_KEY":
        payload = fetcher(
            "https://apps.bea.gov/api/data?"
            + urlencode({
                "UserID": value,
                "method": "GetParameterValues",
                "datasetname": "NIPA",
                "ParameterName": "Frequency",
                "ResultFormat": "JSON",
            }),
            timeout_seconds,
        )
        bea_api = payload.get("BEAAPI") if isinstance(payload, dict) else None
        if bea_api and bea_api.get("Error"):
            return _validation(key, "invalid_key", _redact(str(bea_api.get("Error")), value))
        if not bea_api:
            return _validation(key, "malformed_response", "BEA returned an unexpected response shape.")
        return _validation(key, "valid", "BEA key returned a usable response.")
    if key == "CENSUS_API_KEY":
        payload = fetcher(
            "https://api.census.gov/data/timeseries/eits/marts?"
            + urlencode({
                "get": "cell_value,time",
                "time": "2026-01",
                "category_code": "44X72",
                "key": value,
            }),
            timeout_seconds,
        )
        if isinstance(payload, dict) and payload.get("error"):
            return _validation(key, "invalid_key", _redact(str(payload.get("error")), value))
        if not isinstance(payload, list):
            return _validation(key, "malformed_response", "Census returned an unexpected response shape.")
        return _validation(key, "valid", "Census key returned a usable response.")
    if key == "TIINGO_API_KEY":
        payload = fetcher(
            "https://api.tiingo.com/tiingo/daily/AAPL/prices?"
            + urlencode({"token": value}),
            timeout_seconds,
        )
        if isinstance(payload, dict) and payload.get("error"):
            return _validation(key, "invalid_key", _redact(str(payload.get("error")), value))
        if not isinstance(payload, list):
            return _validation(key, "malformed_response", "Tiingo returned an unexpected response shape.")
        return _validation(key, "valid", "Tiingo key returned a usable EOD price response.")
    if key == "EODHD_API_KEY":
        payload = fetcher(
            "https://eodhd.com/api/eod/AAPL.US?"
            + urlencode({"api_token": value, "fmt": "json", "from": date.today().replace(day=1).isoformat()}),
            timeout_seconds,
        )
        if isinstance(payload, dict) and payload.get("error"):
            message = _redact(str(payload.get("error")), value)
            if "403" in message or "allowed" in message.lower() or "entitlement" in message.lower():
                return _validation(key, "entitlement_error", message)
            if "limit" in message.lower() or "rate" in message.lower():
                return _validation(key, "rate_limited", message)
            return _validation(key, "invalid_key", message)
        if not isinstance(payload, list):
            return _validation(key, "malformed_response", "EODHD returned an unexpected response shape.")
        return _validation(key, "valid", "EODHD key returned a usable EOD price response.")
    return _validation(key, "not_configured", "No validator exists for this key.")


def _validation(key: str, status: str, message: str) -> ValidationResult:
    return ValidationResult(key, SECRET_LABELS.get(key, key), status, message)


def _network_error_message(exc: BaseException) -> tuple[str, str]:
    try:
        from .network_diagnostics import classify_exception

        failure_class, _, message = classify_exception(exc)
        return failure_class, message
    except Exception:
        return "network_error", str(exc)


def _fetch_json(url: str, timeout_seconds: int) -> Any:
    try:
        with urlopen(Request(url, headers={"User-Agent": "US Equity Research Radar"}), timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except HTTPError as exc:
        if exc.code in {401, 403}:
            return {"error": f"HTTP {exc.code}"}
        raise


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _require_allowed_key(key: str) -> None:
    if key.startswith(LLM_PROFILE_SECRET_PREFIX):
        return
    if key not in MANAGED_SECRET_KEYS:
        raise ValueError(f"Unsupported local secret key: {key}")


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")
    in_buffer = ctypes.create_string_buffer(data)
    in_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI is only available on Windows.")
    in_buffer = ctypes.create_string_buffer(data)
    in_blob = _DataBlob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,  # CRYPTPROTECT_UI_FORBIDDEN
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _default_keyring_backend():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        if os.name == "nt":
            return DpapiFileBackend()
        return None
