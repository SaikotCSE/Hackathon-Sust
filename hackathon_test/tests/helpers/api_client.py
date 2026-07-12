"""Thin HTTP client for API-level tests.

The prototype's stack is unconfirmed, so we talk to the running backend over
HTTP rather than importing its modules directly. ``requests`` is the only
required dependency. Each call records (method, path, status, duration_ms) into
the shared metrics sink so performance numbers can be aggregated across layers.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

try:
    import requests  # type: ignore
except ImportError as exc:  # pragma: no cover - guidance for setup
    raise SystemExit(
        "The test suite needs the 'requests' library. "
        "Install with: pip install requests pytest pytest-asyncio"
    ) from exc

from .env import CONFIG, mask_token

log = logging.getLogger(__name__)


@dataclass
class CallRecord:
    method: str
    path: str
    status: int
    duration_ms: float
    label: str = ""
    ok: bool = True


@dataclass
class APIClient:
    base_url: str = field(default_factory=lambda: CONFIG.api_base_url)
    token: Optional[str] = field(default_factory=lambda: CONFIG.api_token)
    call_log: List[CallRecord] = field(default_factory=list)

    # Role -> token lookup. Filled in lazily from env on first use.
    def _resolve_role_token(self, role: str | None) -> str | None:
        if not role or role == "default":
            return self.token
        # Provider-scoped roles reuse the primary token by default; ops /
        # risk split tokens can be added to .env.test if needed.
        if role.startswith("ops_") or role.startswith("risk_"):
            scoped = os.environ.get(f"ROLE_{role.upper()}_TOKEN")
            return scoped or self.token
        return self.token

    # ------------- low level ---------------------------------------------------
    def _headers(self, extra: Optional[Dict[str, str]] = None,
                  token: Optional[str] = None) -> Dict[str, str]:
        h = {"Accept": "application/json"}
        tok = token if token is not None else self.token
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        if extra:
            h.update(extra)
        return h

    def request(
        self,
        method: str,
        path: str,
        *,
        label: str = "",
        expect: Iterable[int] = (200,),
        timeout: Optional[float] = None,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        files: Any = None,
        data: Any = None,
        role: str | None = None,
    ) -> requests.Response:
        url = f"{self.base_url}{path.lstrip('/')}"
        token = self._resolve_role_token(role)
        t0 = time.perf_counter()
        resp = requests.request(
            method=method,
            url=url,
            headers=self._headers(headers, token=token),
            params=params,
            json=json_body,
            files=files,
            data=data,
            timeout=timeout if timeout is not None else CONFIG.timeout_s,
        )
        dt_ms = (time.perf_counter() - t0) * 1000.0
        ok = resp.status_code in set(expect)
        self.call_log.append(
            CallRecord(
                method=method.upper(),
                path=path,
                status=resp.status_code,
                duration_ms=dt_ms,
                label=label or f"{method.upper()} {path}",
                ok=ok,
            )
        )
        if not ok:
            log.debug(
                "API call %s %s -> %s (expected one of %s) in %.1fms",
                method.upper(), path, resp.status_code, sorted(expect), dt_ms,
            )
        return resp

    # ------------- convenience helpers ----------------------------------------
    def get(self, path: str, **kw: Any) -> requests.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> requests.Response:
        return self.request("POST", path, **kw)

    def put(self, path: str, **kw: Any) -> requests.Response:
        return self.request("PUT", path, **kw)

    def patch(self, path: str, **kw: Any) -> requests.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw: Any) -> requests.Response:
        return self.request("DELETE", path, **kw)

    def describe_auth(self) -> str:
        return f"base={self.base_url} token={mask_token(self.token)}"