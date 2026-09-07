"""Shared HTTP client: rate limiting and retry in one place.

Not re-implemented per subcommand, and not requested of the agent in
prose (docs/tool-design-guidance.md §8).

Error bodies are returned, not just codes — an agent can act on
"429, retry after 300s"; it cannot act on "request failed".
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

from .errors import (
    DependencyError,
    EndpointError,
    EndpointUnavailable,
    PhaseContractError,
)

try:  # requests is the one hard HTTP dependency
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

DEFAULT_QPS = 1.0
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_MAX_RESPONSE_BYTES = 100 * 1024 * 1024  # 100 MB
# Note: max_response_bytes=0 rejects all responses (including empty ones)
# because Layer 2 checks ``total > 0`` after the first chunk.
DEFAULT_CHUNK_SIZE = 8192

_RETRY_STATUS = {429, 500, 502, 503, 504}

# Last-request timestamp per host, for polite pacing.
_last_call: dict[str, float] = {}


def _require_requests():
    if requests is None:
        raise DependencyError(
            "the 'requests' package is not installed",
            remedy="install it into the tools environment (see tools/requirements.txt)",
        )
    return requests


#: Set by the CLI before a phase-2 command runs. Non-None means every
#: call through this module raises. Deliberately a module-level latch and
#: not a parameter: a parameter would have to be passed correctly by the
#: very call site the guard exists to catch.
_network_forbidden: str | None = None


def forbid_network(reason: str) -> None:
    """Latch this process offline for the rest of the invocation."""
    global _network_forbidden
    _network_forbidden = reason


def network_forbidden() -> str | None:
    return _network_forbidden


def _pace(url: str, qps: float) -> None:
    if qps <= 0:
        return
    host = urlparse(url).netloc
    interval = 1.0 / qps
    previous = _last_call.get(host)
    now = time.monotonic()
    if previous is not None:
        wait = interval - (now - previous)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def _drain_limited(response, max_bytes=1024):
    """Read up to *max_bytes* of a streamed response into ``response._content``."""
    chunks = []
    total = 0
    for chunk in response.iter_content(chunk_size=max_bytes):
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    response._content = b"".join(chunks)
    response.close()


def request(
    method: str,
    url: str,
    *,
    qps: float = DEFAULT_QPS,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff: float = 2.0,
    expect_status: int = 200,
    tolerate_status: frozenset[int] | set[int] | tuple[int, ...] = (),
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    **kwargs: Any,
):
    """Perform an HTTP request with pacing and bounded retry.

    Raises EndpointUnavailable when the retry budget is exhausted on a
    retryable status, EndpointError on a non-retryable one.

    `tolerate_status` names statuses that are an *answer* rather than a
    failure and should be returned to the caller. A 404 from a
    single-record endpoint is the model case: "this identifier does not
    exist" is exactly what the caller asked, and raising would turn a
    definitive negative into an apparent outage. Tolerated statuses are
    opt-in per call site so that no ordinary request can swallow one by
    accident.
    """
    if _network_forbidden:
        raise PhaseContractError(
            f"a phase-2 command attempted {method} {url}",
            detail=_network_forbidden,
            remedy=(
                "phase 2 reads what phase 1 wrote and applies thresholds to it. "
                "Whatever this call was fetching belongs in the fetch phase, "
                "written to Layer 0 with a sidecar, so that re-analysis stays "
                "offline and the reviewer's re-run does not depend on an "
                "endpoint being reachable"
            ),
        )
    lib = _require_requests()
    last_detail = ""
    last_status: int | None = None
    kwargs["stream"] = True

    for attempt in range(1, max_attempts + 1):
        _pace(url, qps)
        try:
            response = lib.request(method, url, timeout=timeout, **kwargs)
        except Exception as exc:  # transport-level
            last_detail = f"{type(exc).__name__}: {exc}"
            last_status = None
            if attempt == max_attempts:
                raise EndpointUnavailable(
                    f"transport failure calling {url}",
                    detail=last_detail,
                    remedy="check network access and endpoint health, then retry",
                )
            time.sleep(backoff ** attempt)
            continue

        if response.status_code == expect_status or response.status_code in tolerate_status:
            # Layer 1: Content-Length pre-check
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    cl = int(content_length)
                except ValueError:
                    cl = -1
                if cl > max_response_bytes:
                    response.close()
                    raise EndpointError(
                        f"response from {url} exceeds size limit "
                        f"({max_response_bytes} bytes)",
                        detail=f"Content-Length: {content_length}",
                        remedy="if this endpoint legitimately returns large "
                        "responses, pass a higher max_response_bytes",
                    )

            # Layer 2: Streaming read with byte cap
            chunks = []
            total = 0
            for chunk in response.iter_content(chunk_size=DEFAULT_CHUNK_SIZE):
                total += len(chunk)
                if total > max_response_bytes:
                    response.close()
                    raise EndpointError(
                        f"response from {url} exceeds size limit "
                        f"({max_response_bytes} bytes)",
                        detail="size exceeded during streaming read",
                        remedy="if this endpoint legitimately returns large "
                        "responses, pass a higher max_response_bytes",
                    )
                chunks.append(chunk)
            response._content = b"".join(chunks)
            return response

        # Read limited error body from streamed response.
        _drain_limited(response)
        body = (response.text or "")[:500]
        last_status = response.status_code
        last_detail = f"HTTP {response.status_code}: {body}"

        if response.status_code in _RETRY_STATUS:
            retry_after = response.headers.get("Retry-After")
            if attempt == max_attempts:
                raise EndpointUnavailable(
                    f"{url} still returning {response.status_code} after "
                    f"{max_attempts} attempts",
                    detail=last_detail,
                    remedy=(
                        f"endpoint is rate-limited or unhealthy; retry after "
                        f"{retry_after}s" if retry_after else
                        "endpoint is rate-limited or unhealthy; retry later or "
                        "run `dde doctor`"
                    ),
                )
            delay = float(retry_after) if retry_after and retry_after.isdigit() else backoff ** attempt
            time.sleep(delay)
            continue

        raise EndpointError(
            f"{url} returned HTTP {response.status_code}",
            detail=body or None,
        )

    raise EndpointUnavailable(
        f"exhausted attempts calling {url}",
        detail=last_detail or f"last status {last_status}",
    )


def get_json(url: str, **kwargs: Any) -> Any:
    response = request("GET", url, **kwargs)
    try:
        return response.json()
    except ValueError as exc:
        raise EndpointError(
            f"{url} did not return valid JSON", detail=str(exc)
        )


def get_bytes(url: str, **kwargs: Any) -> bytes:
    return request("GET", url, **kwargs).content
