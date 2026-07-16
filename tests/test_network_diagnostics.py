from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from equity_research.models import NetworkProbeStatus
from equity_research.network_diagnostics import (
    _powershell_https_probe,
    build_network_diagnostic_report,
    classify_exception,
    classify_network_issue,
)


class FakeWinRefused(Exception):
    winerror = 10061


class NetworkDiagnosticsTests(unittest.TestCase):
    def test_winerror_10061_is_connection_refused(self) -> None:
        with patch("equity_research.network_diagnostics.getproxies", return_value={}):
            failure_class, retryable, message = classify_exception(
                FakeWinRefused("No connection could be made because the target machine actively refused it"),
                url="https://www.alphavantage.co/query?apikey=secret-value",
            )

        self.assertEqual(failure_class, "connection_refused")
        self.assertTrue(retryable)
        self.assertIn("refused", message.lower())
        self.assertNotIn("secret-value", message)

    def test_local_proxy_refusal_is_proxy_refused(self) -> None:
        failure_class, retryable, message = classify_exception(
            FakeWinRefused("No connection could be made because the target machine actively refused it"),
            url="https://finnhub.io/api/v1/stock/recommendation",
            proxy_url="http://127.0.0.1:7890",
        )

        self.assertEqual(failure_class, "proxy_refused")
        self.assertTrue(retryable)
        self.assertIn("proxy", message.lower())

    def test_powershell_http_error_text_is_entitlement_not_network_error(self) -> None:
        failure_class, retryable, message = classify_exception(
            RuntimeError("ERR The remote server returned an error: (401) Unauthorized."),
            url="https://finnhub.io/api/v1/stock/recommendation",
        )

        self.assertEqual(failure_class, "provider_entitlement")
        self.assertFalse(retryable)
        self.assertIn("401", message)

    def test_powershell_probe_command_uses_valid_try_catch_shape(self) -> None:
        seen: dict[str, str] = {}

        def fake_run(command, **kwargs):
            seen["script"] = command[-1]
            return SimpleNamespace(returncode=0, stdout="OK 200", stderr="")

        with patch("equity_research.network_diagnostics.subprocess.run", side_effect=fake_run):
            probe = _powershell_https_probe(
                "Alpha Vantage",
                "https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=IBM&apikey=demo",
                5,
                "2026-07-08T00:00:00+00:00",
            )

        self.assertEqual(probe.status, "ok")
        self.assertIn("} catch", seen["script"])
        self.assertNotIn("}} catch", seen["script"])

    def test_general_outbound_block_when_all_tcp_checks_fail(self) -> None:
        probes = [
            _probe("Alpha Vantage", "tcp_443", "failed", "connection_refused"),
            _probe("SEC EDGAR", "tcp_443", "failed", "connection_refused"),
            _probe("Neutral HTTPS", "tcp_443", "failed", "timeout"),
        ]

        self.assertEqual(classify_network_issue(probes), "general_outbound_block")

    def test_python_only_failure_when_powershell_succeeds(self) -> None:
        probes = [
            _probe("Alpha Vantage", "python_https", "failed", "connection_refused"),
            _probe("Alpha Vantage", "powershell_https", "ok", "ok"),
        ]

        self.assertEqual(classify_network_issue(probes), "python_only_failure")

    def test_entitlement_and_shared_timeout_do_not_create_python_only_failure(self) -> None:
        probes = [
            _probe("Alpha Vantage", "python_https", "ok", "ok"),
            _probe("Alpha Vantage", "powershell_https", "ok", "ok"),
            _probe("Finnhub", "python_https", "failed", "provider_entitlement"),
            _probe("Finnhub", "powershell_https", "failed", "provider_entitlement"),
            _probe("FRED", "python_https", "failed", "timeout"),
            _probe("FRED", "powershell_https", "failed", "timeout"),
            _probe("Neutral HTTPS", "python_https", "ok", "ok"),
            _probe("Neutral HTTPS", "powershell_https", "ok", "ok"),
        ]

        self.assertEqual(classify_network_issue(probes), "network_available")

    def test_provider_specific_block_when_neutral_hosts_work(self) -> None:
        probes = [
            _probe("SEC EDGAR", "python_https", "ok", "ok"),
            _probe("Neutral HTTPS", "python_https", "ok", "ok"),
            _probe("Alpha Vantage", "python_https", "failed", "provider_blocked"),
        ]

        self.assertEqual(classify_network_issue(probes), "provider_specific_block")

    def test_all_ok_https_is_network_available(self) -> None:
        probes = [
            _probe("Alpha Vantage", "python_https", "ok", "ok"),
            _probe("SEC EDGAR", "python_https", "ok", "ok"),
            _probe("Neutral HTTPS", "python_https", "ok", "ok"),
        ]

        self.assertEqual(classify_network_issue(probes), "network_available")

    def test_report_contains_suggested_actions_and_redacted_proxy(self) -> None:
        report = build_network_diagnostic_report(
            [_probe("Alpha Vantage", "tcp_443", "failed", "connection_refused")],
            proxy_state={"env:HTTPS_PROXY": "http://[redacted]:[redacted]@127.0.0.1:7890"},
            observed_at="2026-07-08T00:00:00+00:00",
        )

        self.assertEqual(report.status, "Available")
        self.assertTrue(report.suggested_actions)
        self.assertNotIn("secret", str(report.proxy_state))


def _probe(provider: str, check_type: str, status: str, failure_class: str) -> NetworkProbeStatus:
    return NetworkProbeStatus(
        provider=provider,
        endpoint_host="example.test",
        check_type=check_type,
        status=status,
        failure_class=failure_class,
        message=failure_class,
        retryable=status != "ok",
        observed_at="2026-07-08T00:00:00+00:00",
    )


if __name__ == "__main__":
    unittest.main()
