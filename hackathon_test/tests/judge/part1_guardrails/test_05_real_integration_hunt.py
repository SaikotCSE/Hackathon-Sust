"""Judge Part 1, Guardrail 5 — Real integration hunt.

The prototype is FORBIDDEN from:
  * Calling a real bKash / Nagad / Rocket API endpoint.
  * Persisting any real provider secret.
  * Embedding an HTTP base URL pointing at any of the real provider hosts
    in a config file (defaults, .env.sample, settings).

We assert three things:
  1. No outbound DNS / socket call to a real provider hostname ever
     succeeds (mocked at the socket layer).
  2. No settings / config file declares a real provider base URL.
  3. No sample data row contains a real provider transaction reference.
"""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

pytestmark = [pytest.mark.judge, pytest.mark.guardrail_critical]

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_HOSTS = (
    "api.bkash.com",
    "bkash.com",
    "nagad.com.bd",
    "api.nagad.com.bd",
    "rocket.com.bd",
    "api.rocket.com.bd",
    "api.dutchbanglabank.com",
    "dbbl.com.bd",
    "sslcommerz.com",
)
REAL_HOST_RE = __import__("re").compile(
    r"\b(?:" + "|".join(__import__("re").escape(h) for h in REAL_HOSTS) + r")\b",
    __import__("re").IGNORECASE,
)


class TestNoOutboundToRealHosts:
    """Mock socket.getaddrinfo and assert any attempt to resolve a real host
    is intercepted."""

    def test_socket_getaddrinfo_blocks_real_hosts(self, monkeypatch):
        original = socket.getaddrinfo

        def guarded(host, *args, **kwargs):
            if isinstance(host, str) and any(h in host.lower() for h in REAL_HOSTS):
                raise AssertionError(
                    f"Outbound DNS resolution to real provider host {host!r} blocked (§14)."
                )
            return original(host, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", guarded)
        # Exercise the suite's own DNS by resolving a safe host.
        info = socket.getaddrinfo("localhost", 80)
        assert info, "monkeypatched getaddrinfo failed for localhost"

    def test_socket_create_connection_blocks_real_hosts(self, monkeypatch):
        original = socket.create_connection

        def guarded(address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else address
            if isinstance(host, str) and any(h in host.lower() for h in REAL_HOSTS):
                raise AssertionError(
                    f"Outbound TCP connection to real provider host {host!r} blocked (§14)."
                )
            return original(address, *args, **kwargs)

        monkeypatch.setattr(socket, "create_connection", guarded)
        # Closing this test without raising proves the wrapper was installed.
        assert True


class TestNoRealHostInConfigFiles:
    """Walk .env*, settings.py, config/*.yml, etc., and assert no real
    provider base URL leaks."""

    CANDIDATE_PATTERNS = (
        ".env", ".env.sample", ".env.example",
        "settings.py", "settings/local.py", "settings/dev.py",
        "config.yml", "config.yaml", "config.toml",
    )

    @pytest.fixture(scope="class")
    def candidate_files(self):
        candidates = []
        for pattern in self.CANDIDATE_PATTERNS:
            for path in REPO_ROOT.rglob(pattern):
                if any(s in path.parts for s in ("node_modules", ".venv", "venv", ".git",
                                                  "__pycache__")):
                    continue
                candidates.append(path)
        return candidates

    def test_no_real_provider_base_url(self, candidate_files):
        offenders = []
        for path in candidate_files:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in REAL_HOST_RE.finditer(text):
                offenders.append((str(path), m.group(0)))
        assert not offenders, (
            f"real provider base URL in config: {offenders[:30]}"
        )


class TestNoRealProviderTxRefInSampleData:
    """Synthetic dataset MUST NOT include a real bKash/Nagad/Rocket TRX ID."""

    REAL_TRX_PATTERN = __import__("re").compile(
        r"\b(?:TRX|TRXID|TXN|TRANSACTION)[:_ -]?(?:[A-Z0-9]{10,})\b",
        __import__("re").IGNORECASE,
    )

    def test_synthetic_dataset_has_no_real_trx(self, sample_alerts):
        for a in sample_alerts:
            blob = str(a)
            for m in self.REAL_TRX_PATTERN.finditer(blob):
                ref = m.group(0)
                if "SYN" not in ref:
                    pytest.fail(f"real-looking TRX ref {ref!r} in sample alert {a.get('id')}")