from __future__ import annotations

import json
import os
import platform
import re
import socket
import ssl
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, getproxies, urlopen

from . import config
from .models import NetworkDiagnosticReport, NetworkProbeStatus


DEFAULT_NETWORK_TARGETS: tuple[tuple[str, str], ...] = (
    ("Alpha Vantage", "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey=demo"),
    ("Finnhub", "https://finnhub.io/api/v1/stock/recommendation?symbol=AAPL"),
    ("Nasdaq public", "https://api.nasdaq.com/api/analyst/AAPL/earnings-forecast"),
    ("TradingView scanner", "https://scanner.tradingview.com/america/scan"),
    ("SEC EDGAR", "https://www.sec.gov/"),
    ("FRED", "https://api.stlouisfed.org/"),
    ("Neutral HTTPS", "https://example.com/"),
)

PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)

FetchText = Callable[[str, int], Tuple[Optional[int], str]]


def run_network_diagnostics(
    *,
    timeout_seconds: int | None = None,
    include_powershell: bool = True,
    output_path: Path | None = None,
    fetch_text: FetchText | None = None,
) -> NetworkDiagnosticReport:
    timeout = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 10)
    observed_at = _now()
    proxy_state = collect_proxy_state()
    probes: list[NetworkProbeStatus] = []
    fetcher = fetch_text or _fetch_text
    for provider, url in DEFAULT_NETWORK_TARGETS:
        host = urlparse(url).hostname or url
        probes.append(_dns_probe(provider, host, observed_at))
        probes.append(_tcp_probe(provider, host, 443, timeout, observed_at))
        probes.append(_python_https_probe(provider, url, timeout, observed_at, fetcher))
        if include_powershell and platform.system().lower() == "windows":
            probes.append(_powershell_https_probe(provider, url, timeout, observed_at))
    report = build_network_diagnostic_report(
        probes,
        proxy_state=proxy_state,
        observed_at=observed_at,
    )
    if output_path:
        write_network_diagnostic_report(report, output_path)
    return report


def collect_proxy_state() -> dict[str, str]:
    state: dict[str, str] = {}
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            state[f"env:{key}"] = _redact(value)
    try:
        for scheme, proxy in getproxies().items():
            if proxy:
                state[f"urllib:{scheme}"] = _redact(proxy)
    except Exception:
        pass
    if platform.system().lower() == "windows":
        state.update(_windows_proxy_state())
    if not state:
        state["proxy"] = "No process/user proxy detected by the diagnostic script."
    return state


def runtime_context() -> dict[str, str]:
    context = {
        "pid": str(os.getpid()),
        "python_executable": _redact(sys.executable),
        "python_version": platform.python_version(),
        "working_directory": _redact(os.getcwd()),
    }
    if sys.prefix:
        context["python_prefix"] = _redact(sys.prefix)
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        context["virtual_env"] = _redact(virtual_env)
    return context


def build_network_diagnostic_report(
    probes: list[NetworkProbeStatus],
    *,
    proxy_state: dict[str, str] | None = None,
    observed_at: str | None = None,
) -> NetworkDiagnosticReport:
    observed = observed_at or _now()
    network_class = classify_network_issue(probes)
    suggested = _suggested_actions(network_class)
    return NetworkDiagnosticReport(
        status="Available",
        network_class=network_class,
        summary=_summary(network_class),
        observed_at=observed,
        proxy_state=proxy_state or {},
        runtime_context=runtime_context(),
        probes=probes,
        suggested_actions=suggested,
        data_gaps=[] if probes else ["No network probes were run."],
    )


def classify_network_issue(probes: list[NetworkProbeStatus]) -> str:
    active = [probe for probe in probes if probe.check_type in {"tcp_443", "python_https", "powershell_https"}]
    if not active:
        return "unknown"
    if any(probe.failure_class == "proxy_refused" for probe in active):
        return "proxy_refused"
    python = [probe for probe in active if probe.check_type == "python_https"]
    powershell = [probe for probe in active if probe.check_type == "powershell_https"]
    if python and powershell:
        for py_probe in python:
            if not _is_transport_failure(py_probe):
                continue
            matching_ps = [probe for probe in powershell if probe.provider == py_probe.provider]
            if any(probe.status == "ok" or probe.failure_class == "provider_entitlement" for probe in matching_ps):
                return "python_only_failure"
    tcp = [probe for probe in active if probe.check_type == "tcp_443"]
    if tcp and all(probe.status != "ok" for probe in tcp):
        return "general_outbound_block"
    https = [probe for probe in active if probe.check_type.endswith("https")]
    failed_https = [probe for probe in https if probe.status != "ok"]
    if failed_https and all(probe.failure_class == "provider_entitlement" for probe in failed_https):
        return "provider_entitlement"
    neutral_ok = any(probe.provider in {"SEC EDGAR", "FRED", "Neutral HTTPS"} and probe.status == "ok" for probe in active)
    provider_failures = [
        probe for probe in active
        if probe.provider in {"Alpha Vantage", "Finnhub", "Nasdaq public", "TradingView scanner"}
        and probe.status != "ok"
        and probe.failure_class not in {"provider_entitlement", "http_error"}
    ]
    if neutral_ok and provider_failures:
        return "provider_specific_block"
    if any(probe.failure_class == "captive_portal_or_wifi_block" for probe in active):
        return "captive_portal_or_wifi_block"
    if any(probe.status == "ok" for probe in active):
        return "network_available"
    return "unknown_network_failure"


def _is_transport_failure(probe: NetworkProbeStatus) -> bool:
    return probe.status != "ok" and probe.failure_class in {
        "connection_refused",
        "proxy_refused",
        "dns_failed",
        "tls_failed",
        "timeout",
        "network_error",
    }


def classify_exception(
    exc: BaseException,
    *,
    url: str = "",
    proxy_url: str = "",
    http_status: int | None = None,
) -> tuple[str, bool, str]:
    message = _redact(_exception_text(exc))
    lower = message.lower()
    proxy = proxy_url or _proxy_for_url(url)
    proxy_host = _host(proxy)
    if proxy_host in {"localhost", "127.0.0.1", "::1"} and _contains_refused(exc, lower):
        return "proxy_refused", True, "Local proxy is configured but refused the connection."
    text_status = _http_status_from_text(lower)
    if text_status is not None:
        if text_status in {401, 403, 429}:
            return "provider_entitlement", False, f"Provider returned HTTP {text_status}."
        return "http_error", text_status >= 500, f"Provider returned HTTP {text_status}."
    if isinstance(exc, HTTPError) or http_status is not None:
        status = http_status or getattr(exc, "code", None)
        if status in {401, 403, 429}:
            return "provider_entitlement", False, f"Provider returned HTTP {status}."
        return "http_error", status is not None and int(status) >= 500, f"Provider returned HTTP {status}."
    if isinstance(exc, ssl.SSLError) or "certificate" in lower or "ssl" in lower or "tls" in lower:
        return "tls_failed", True, "TLS/certificate validation failed."
    if isinstance(exc, TimeoutError) or isinstance(exc, socket.timeout) or "timed out" in lower or "timeout" in lower:
        return "timeout", True, "The request timed out."
    if isinstance(exc, socket.gaierror) or "getaddrinfo failed" in lower or "name or service not known" in lower:
        return "dns_failed", True, "DNS resolution failed."
    if _contains_refused(exc, lower):
        return "connection_refused", True, "The target actively refused the connection."
    if "forbidden by its access permissions" in lower or "accessdenied" in lower:
        return "connection_refused", True, "Socket access was denied by the local network, firewall, or sandbox."
    return "network_error", True, message


def network_message_hint(message: str) -> tuple[str, str] | None:
    failure_class, retryable, diagnostic = classify_exception(RuntimeError(message))
    if failure_class in {
        "connection_refused",
        "proxy_refused",
        "dns_failed",
        "tls_failed",
        "timeout",
        "captive_portal_or_wifi_block",
        "provider_blocked",
        "network_error",
    }:
        suffix = " Retryable." if retryable else ""
        return failure_class, f"{diagnostic} {_suggested_fix(failure_class)}{suffix}".strip()
    return None


def write_network_diagnostic_report(report: NetworkDiagnosticReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), indent=2, sort_keys=True), encoding="utf-8")


def default_report_path(base_dir: Path | None = None) -> Path:
    root = base_dir or Path("data")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return root / f"network_diagnostics_{stamp}.json"


def _dns_probe(provider: str, host: str, observed_at: str) -> NetworkProbeStatus:
    try:
        socket.getaddrinfo(host, 443)
        return _probe(provider, host, "dns", "ok", "ok", "DNS resolved.", False, observed_at)
    except Exception as exc:
        failure, retryable, message = classify_exception(exc)
        return _probe(provider, host, "dns", "failed", failure, message, retryable, observed_at)


def _tcp_probe(provider: str, host: str, port: int, timeout: int, observed_at: str) -> NetworkProbeStatus:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return _probe(provider, host, "tcp_443", "ok", "ok", "TCP 443 connection succeeded.", False, observed_at)
    except Exception as exc:
        failure, retryable, message = classify_exception(exc, url=f"https://{host}/")
        return _probe(provider, host, "tcp_443", "failed", failure, message, retryable, observed_at)


def _python_https_probe(provider: str, url: str, timeout: int, observed_at: str, fetcher: FetchText) -> NetworkProbeStatus:
    host = urlparse(url).hostname or url
    try:
        status, text = fetcher(url, timeout)
    except Exception as exc:
        failure, retryable, message = classify_exception(exc, url=url)
        return _probe(provider, host, "python_https", "failed", failure, message, retryable, observed_at)
    failure = _content_failure_class(text)
    if failure:
        return _probe(provider, host, "python_https", "failed", failure, _summary(failure), True, observed_at, http_status=status)
    if status in {401, 403, 429}:
        return _probe(provider, host, "python_https", "failed", "provider_entitlement", f"Provider returned HTTP {status}.", False, observed_at, http_status=status)
    if status and status >= 400:
        return _probe(provider, host, "python_https", "failed", "http_error", f"Provider returned HTTP {status}.", status >= 500, observed_at, http_status=status)
    return _probe(provider, host, "python_https", "ok", "ok", f"HTTPS returned HTTP {status or 'unknown'}.", False, observed_at, http_status=status)


def _powershell_https_probe(provider: str, url: str, timeout: int, observed_at: str) -> NetworkProbeStatus:
    host = urlparse(url).hostname or url
    safe_url = _redact(url)
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "$ProgressPreference='SilentlyContinue'; "
            f"try {{ $r=Invoke-WebRequest -Uri '{safe_url}' -Method Get -TimeoutSec {max(1, timeout)} -UseBasicParsing; "
            "Write-Output ('OK ' + [int]$r.StatusCode) } "
            "catch { Write-Output ('ERR ' + $_.Exception.Message); exit 2 }"
        ),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 3)
    except Exception as exc:
        failure, retryable, message = classify_exception(exc, url=url)
        return _probe(provider, host, "powershell_https", "failed", failure, message, retryable, observed_at)
    output = _redact((result.stdout or result.stderr or "").strip())
    if result.returncode == 0 and output.startswith("OK"):
        return _probe(provider, host, "powershell_https", "ok", "ok", output, False, observed_at)
    failure, retryable, message = classify_exception(RuntimeError(output or "PowerShell HTTPS request failed."), url=url)
    return _probe(provider, host, "powershell_https", "failed", failure, message, retryable, observed_at)


def _fetch_text(url: str, timeout: int) -> tuple[int | None, str]:
    request = Request(url, headers={"User-Agent": config.APP_NAME})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(4096).decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        return exc.code, body


def _probe(
    provider: str,
    host: str,
    check_type: str,
    status: str,
    failure_class: str,
    message: str,
    retryable: bool,
    observed_at: str,
    *,
    http_status: int | None = None,
) -> NetworkProbeStatus:
    proxy_used = _proxy_for_url(f"https://{host}/")
    return NetworkProbeStatus(
        provider=provider,
        endpoint_host=host,
        check_type=check_type,
        status=status,
        failure_class=failure_class,
        message=_redact(message),
        retryable=retryable,
        proxy_used=_redact(proxy_used),
        observed_at=observed_at,
        suggested_fix=_suggested_fix(failure_class),
        http_status=http_status,
    )


def _content_failure_class(text: str) -> str:
    lower = (text or "")[:2000].lower()
    if any(token in lower for token in ("captive portal", "airport", "wifi login", "log in to", "login to wi-fi")):
        return "captive_portal_or_wifi_block"
    if any(token in lower for token in ("captcha", "verify you are human", "browser verification", "access denied")):
        return "provider_blocked"
    return ""


def _windows_proxy_state() -> dict[str, str]:
    state: dict[str, str] = {}
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            for name in ("ProxyEnable", "ProxyServer", "AutoConfigURL"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                if value not in (None, ""):
                    state[f"windows:{name}"] = _redact(str(value))
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["netsh", "winhttp", "show", "proxy"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout:
            state["windows:winhttp"] = _redact(" ".join(result.stdout.split()))
    except Exception:
        pass
    return state


def _proxy_for_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        proxies = getproxies()
        return proxies.get(parsed.scheme) or proxies.get("all") or ""
    except Exception:
        return ""


def _contains_refused(exc: BaseException, lower_text: str) -> bool:
    winerror = getattr(exc, "winerror", None)
    if winerror == 10061:
        return True
    reason = getattr(exc, "reason", None)
    if reason is not None and reason is not exc:
        if getattr(reason, "winerror", None) == 10061:
            return True
        if isinstance(reason, ConnectionRefusedError):
            return True
    return any(token in lower_text for token in ("winerror 10061", "actively refused", "connection refused"))


def _exception_text(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if reason and reason is not exc:
        return f"{exc}; reason={reason}"
    return str(exc)


def _http_status_from_text(lower_text: str) -> int | None:
    match = re.search(r"\((\d{3})\)", lower_text) or re.search(r"\bhttp\s+(\d{3})\b", lower_text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _redact(text: str) -> str:
    redacted = str(text or "")
    secrets = [
        config.FMP_API_KEY,
        config.ALPHAVANTAGE_API_KEY,
        config.FINNHUB_API_KEY,
        config.FRED_API_KEY,
        config.BEA_API_KEY,
        config.CENSUS_API_KEY,
        config.WISBURG_API_KEY,
        config.OPENAI_API_KEY,
        config.ANTHROPIC_API_KEY,
        config.QWEN_API_KEY,
        config.KIMI_API_KEY,
        config.DEEPSEEK_API_KEY,
    ]
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = re.sub(r"(?i)(apikey=|api_key=|token=|key=)[^&\s]+", r"\1[redacted]", redacted)
    redacted = re.sub(r"(?i)(https?://)([^:/@\s]+):([^@\s]+)@", r"\1[redacted]:[redacted]@", redacted)
    redacted = re.sub(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._-]+", r"\1[redacted]", redacted)
    return redacted


def _summary(network_class: str) -> str:
    return {
        "proxy_refused": "A configured local proxy appears to be refusing connections.",
        "captive_portal_or_wifi_block": "The network may require browser login or airport Wi-Fi authentication.",
        "general_outbound_block": "Outbound HTTPS appears blocked or refused across multiple hosts.",
        "provider_specific_block": "General HTTPS works, but one or more research providers are blocked.",
        "python_only_failure": "PowerShell/browser-style HTTPS works, but Python urllib fails.",
        "provider_entitlement": "Network connectivity works, but providers returned key/rate-limit/entitlement responses.",
        "network_available": "Network checks found usable HTTPS connectivity.",
        "unknown_network_failure": "Network checks failed, but the failure pattern was not specific enough to classify.",
    }.get(network_class, "Network status could not be classified.")


def _suggested_fix(failure_class: str) -> str:
    return {
        "proxy_refused": "Disable stale proxy settings or start/configure the local proxy/VPN.",
        "connection_refused": "Restart Streamlit after proxy changes; then try airport Wi-Fi login, VPN, a different network, or offline/cached mode.",
        "dns_failed": "Try a different DNS/network or reconnect Wi-Fi.",
        "tls_failed": "Check corporate/airport TLS inspection, certificates, or Python certificate configuration.",
        "timeout": "Retry, reduce enabled live providers, or use cached/manual sources.",
        "provider_entitlement": "Check API key validity, entitlement, and rate limits.",
        "http_error": "Provider is reachable but returned an HTTP error; inspect provider status.",
        "provider_blocked": "Provider returned bot/captcha/access-block page; try later or a different network.",
        "captive_portal_or_wifi_block": "Open a browser and complete airport Wi-Fi/captive portal login.",
        "ok": "",
    }.get(failure_class, "Inspect network, proxy/VPN, and provider status.")


def _suggested_actions(network_class: str) -> list[str]:
    if network_class == "proxy_refused":
        return [
            "Check HTTP_PROXY/HTTPS_PROXY and Windows proxy settings for localhost or closed proxy ports.",
            "Disable stale proxy settings or start the configured proxy/VPN.",
            "Retry diagnostics from the same PowerShell venv used for Streamlit.",
        ]
    if network_class == "captive_portal_or_wifi_block":
        return [
            "Open a browser and complete the airport Wi-Fi login page.",
            "Retry the diagnostic after login succeeds.",
            "Use CSV/manual/cache mode until live HTTPS is available.",
        ]
    if network_class == "general_outbound_block":
        return [
            "Try a VPN, mobile hotspot, or a different Wi-Fi network.",
            "Run research with cached SQLite, price CSVs, transcript imports, and consensus CSV snapshots.",
            "Do not interpret missing consensus or price data as a research signal.",
        ]
    if network_class == "provider_specific_block":
        return [
            "Keep SEC/local research running and label affected providers as network-blocked.",
            "Try a different network or provider fallback for consensus/market data.",
            "Seed consensus CSV snapshots if market-capture evidence is needed immediately.",
        ]
    if network_class == "python_only_failure":
        return [
            "Compare Python certificate/proxy settings with PowerShell/browser settings.",
            "Check REQUESTS_CA_BUNDLE, SSL_CERT_FILE, and Python trust store.",
            "Use the app's cached/manual path while fixing Python HTTPS.",
        ]
    if network_class == "provider_entitlement":
        return [
            "Connectivity works; check provider keys, rate limits, and endpoint entitlements.",
            "Do not rotate keys solely because of transport-level diagnostics.",
        ]
    if network_class == "network_available":
        return [
            "Network checks from this process look usable.",
            "If the app still shows WinError 10061, fully stop and restart Streamlit from the same venv terminal.",
            "Then re-test provider keys and consensus sources.",
        ]
    return [
        "Retry diagnostics from the Streamlit terminal.",
        "Check browser access, VPN/proxy state, and cached/manual source availability.",
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
