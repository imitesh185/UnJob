"""Policy-enforcing HTTP access for discovery.

Every outbound request made by discovery goes through ``SafeFetcher``. It only reaches
public HTTP(S) addresses, re-validates every redirect hop, verifies the connected peer
address, honours robots.txt (RFC 9309), paces requests per host, retries transient
failures with bounded exponential backoff, and caps response sizes. Failures are raised
as classified ``FetchError`` values; they are never converted into empty successes.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import random
import socket
import time
import urllib.robotparser
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.config import Settings

ALLOWED_PORTS = {None, 80, 443}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_REDIRECTS = 5
ROBOTS_MAX_BYTES = 512_000
ROBOTS_TTL_SECONDS = 6 * 3600
ROBOTS_FAILURE_TTL_SECONDS = 600
MAX_CRAWL_DELAY_SECONDS = 30.0
BLOCKED_CODES = {"BLOCKED", "ROBOTS_DISALLOWED", "RATE_LIMITED", "CHALLENGE"}
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")

# Process-wide politeness state, shared by every fetcher in a worker process.
_HOST_NEXT_ALLOWED: dict[str, float] = {}
_HOST_COOLDOWN_UNTIL: dict[str, float] = {}
_HOST_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_ROBOTS_CACHE: dict[str, tuple[float, Any]] = {}

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """A classified fetch failure.

    Codes: INVALID_URL, UNSAFE_URL, ROBOTS_DISALLOWED, BLOCKED, CHALLENGE, RATE_LIMITED,
    NOT_FOUND, HTTP_ERROR, NETWORK_ERROR, TOO_LARGE, INVALID_RESPONSE.
    """

    def __init__(self, code: str, message: str, url: str, status_code: int | None = None):
        super().__init__(message)
        self.code = code
        self.url = url
        self.status_code = status_code

    @property
    def blocked(self) -> bool:
        return self.code in BLOCKED_CODES

    def __str__(self) -> str:
        return f"{self.code}: {self.args[0]} ({self.url})"


class BudgetExhausted(Exception):
    def __init__(self, budget: str) -> None:
        super().__init__(f"Run budget '{budget}' is exhausted.")
        self.budget = budget


@dataclass
class FetchResponse:
    url: str
    status_code: int
    headers: httpx.Headers
    content: bytes

    @property
    def text(self) -> str:
        charset = None
        for part in self.headers.get("content-type", "").split(";"):
            if part.strip().lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip('"')
        try:
            return self.content.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.content)
        except ValueError as exc:
            raise FetchError(
                "INVALID_RESPONSE", "Response was not valid JSON.", self.url, self.status_code
            ) from exc


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def validate_public_url(url: str) -> str:
    """Syntactic SSRF checks. DNS answers and the connected peer are checked per request."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError as exc:
        raise FetchError("INVALID_URL", "URL could not be parsed.", url) from exc
    if parts.scheme not in {"http", "https"}:
        raise FetchError("INVALID_URL", "Only HTTP and HTTPS URLs are allowed.", url)
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        raise FetchError("INVALID_URL", "URL has no host.", url)
    if parts.username or parts.password:
        raise FetchError("UNSAFE_URL", "URLs with embedded credentials are not allowed.", url)
    if port not in ALLOWED_PORTS:
        raise FetchError("UNSAFE_URL", "Only default HTTP/HTTPS ports are allowed.", url)
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        raise FetchError("UNSAFE_URL", "Local and internal host names are not allowed.", url)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if "." not in host:
            raise FetchError(
                "UNSAFE_URL", "Single-label host names are not allowed.", url
            ) from None
    else:
        if not _is_public_address(host):
            raise FetchError("UNSAFE_URL", "Only public IP addresses are allowed.", url)
    return url.strip()


def looks_like_challenge(text: str) -> bool:
    sample = text[:20_000].lower()
    markers = (
        "captcha",
        "cf-chl",
        "challenge-platform",
        "unfortunately, bots use duckduckgo too",
        "anomaly-modal",
        "are you a robot",
        "verify you are human",
    )
    return any(marker in sample for marker in markers)


async def _system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class SafeFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        verify_peer: bool | None = None,
        on_request: Callable[[str], None] | None = None,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        respect_robots: bool = True,
        trusted_origins: set[str] | None = None,
    ) -> None:
        self.settings = settings
        self._resolver = resolver or _system_resolver
        # Mock transports used in tests have no network stream to inspect.
        self._verify_peer = transport is None if verify_peer is None else verify_peer
        self._on_request = on_request
        self._sleep = sleep
        self._respect_robots = respect_robots
        # Exact origins configured by the operator (for example a self-hosted SearXNG).
        # They are never derived from API input.
        self._trusted_origins = {origin.rstrip("/").lower() for origin in trusted_origins or set()}
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(settings.request_timeout_seconds, connect=10.0),
            follow_redirects=False,
            trust_env=False,
            headers={
                "User-Agent": settings.discovery_user_agent,
                "Accept-Language": "en;q=0.9",
            },
        )

    async def __aenter__(self) -> SafeFetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def set_request_hook(self, hook: Callable[[str], None] | None) -> None:
        self._on_request = hook

    @staticmethod
    def host_of(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    @staticmethod
    def origin_of(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}".lower()

    def _trusted(self, url: str) -> bool:
        return self.origin_of(url) in self._trusted_origins

    def cooldown(self, host: str, seconds: float | None = None) -> None:
        duration = (
            self.settings.discovery_rate_limit_cooldown_seconds if seconds is None else seconds
        )
        _HOST_COOLDOWN_UNTIL[host] = time.monotonic() + duration

    @staticmethod
    def cooldown_remaining(host: str) -> float:
        return max(0.0, _HOST_COOLDOWN_UNTIL.get(host, 0.0) - time.monotonic())

    async def get(self, url: str, **kwargs: Any) -> FetchResponse:
        return await self.request("GET", url, **kwargs)

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json", **kwargs.pop("headers", {})}
        return (await self.get(url, headers=headers, **kwargs)).json()

    async def post_json(self, url: str, body: Any, **kwargs: Any) -> Any:
        headers = {"Accept": "application/json", **kwargs.pop("headers", {})}
        response = await self.request("POST", url, json_body=body, headers=headers, **kwargs)
        return response.json()

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        max_bytes: int | None = None,
        min_interval: float | None = None,
        check_robots: bool = True,
        count_request: bool = True,
    ) -> FetchResponse:
        current = str(httpx.URL(url, params=params)) if params else url
        limit = max_bytes or self.settings.discovery_max_response_bytes
        for _ in range(MAX_REDIRECTS + 1):
            trusted = self._trusted(current)
            if not trusted:
                validate_public_url(current)
            # The budget is charged before any network access, including robots.txt.
            if count_request and self._on_request:
                self._on_request(current)
            if check_robots and self._respect_robots and not trusted:
                await self._enforce_robots(current)
            response = await self._send_with_retries(
                method, current, headers, json_body, limit, min_interval
            )
            location = response.headers.get("location")
            if response.status_code in REDIRECT_STATUSES and location:
                current = urljoin(current, location)
                if response.status_code == 303 or (
                    response.status_code in {301, 302} and method == "POST"
                ):
                    method, json_body = "GET", None
                continue
            return self._classify(response)
        raise FetchError("HTTP_ERROR", "Too many redirects.", url)

    def _classify(self, response: FetchResponse) -> FetchResponse:
        code = response.status_code
        if code < 400:
            return response
        if code in {404, 410}:
            raise FetchError("NOT_FOUND", f"HTTP {code}", response.url, code)
        if code in {401, 403}:
            kind = "CHALLENGE" if looks_like_challenge(response.text) else "BLOCKED"
            raise FetchError(kind, f"Access restricted (HTTP {code}).", response.url, code)
        if code == 429:
            self.cooldown(self.host_of(response.url))
            raise FetchError("RATE_LIMITED", "Rate limited (HTTP 429).", response.url, code)
        raise FetchError("HTTP_ERROR", f"HTTP {code}", response.url, code)

    async def _send_with_retries(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        json_body: Any,
        max_bytes: int,
        min_interval: float | None,
    ) -> FetchResponse:
        host = self.host_of(url)
        attempts = self.settings.discovery_max_retries + 1
        for attempt in range(attempts):
            if _HOST_COOLDOWN_UNTIL.get(host, 0) > time.monotonic():
                raise FetchError(
                    "RATE_LIMITED", "Host is cooling down after rate limiting or blocking.", url
                )
            await self._pace(host, min_interval)
            try:
                response = await self._send_once(method, url, headers, json_body, max_bytes)
            except FetchError:
                raise
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if attempt + 1 >= attempts:
                    raise FetchError(
                        "NETWORK_ERROR", f"{exc.__class__.__name__} after retries.", url
                    ) from exc
                await self._sleep(self._backoff(attempt))
                continue
            if response.status_code in RETRYABLE_STATUSES and attempt + 1 < attempts:
                delay = self._retry_after(response.headers.get("retry-after"))
                if delay is not None and delay > 60:
                    return response
                await self._sleep(delay if delay is not None else self._backoff(attempt))
                continue
            return response
        raise FetchError("NETWORK_ERROR", "Request failed after retries.", url)

    async def _send_once(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        json_body: Any,
        max_bytes: int,
    ) -> FetchResponse:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
        trusted = self._trusted(url)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not trusted:
                try:
                    addresses = await self._resolver(host, port)
                except OSError as exc:
                    raise FetchError("NETWORK_ERROR", "DNS resolution failed.", url) from exc
                if not addresses:
                    raise FetchError("NETWORK_ERROR", "DNS returned no addresses.", url) from None
                if not all(_is_public_address(address) for address in addresses):
                    raise FetchError(
                        "UNSAFE_URL", "Host resolves to a non-public address.", url
                    ) from None
        async with self._client.stream(method, url, headers=headers, json=json_body) as response:
            if self._verify_peer and not trusted:
                stream = response.extensions.get("network_stream")
                peer = stream.get_extra_info("server_addr") if stream is not None else None
                if not peer or not _is_public_address(str(peer[0])):
                    raise FetchError(
                        "UNSAFE_URL", "Connected peer address is not a public address.", url
                    )
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise FetchError("TOO_LARGE", f"Response exceeds {max_bytes} bytes.", url)
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("TOO_LARGE", f"Response exceeds {max_bytes} bytes.", url)
                chunks.append(chunk)
            return FetchResponse(
                url=str(response.url),
                status_code=response.status_code,
                headers=response.headers,
                content=b"".join(chunks),
            )

    async def _pace(self, host: str, min_interval: float | None) -> None:
        interval = max(
            self.settings.discovery_host_min_interval_seconds,
            min_interval or 0,
            self._robots_delay(host),
        )
        key = (id(asyncio.get_running_loop()), host)
        lock = _HOST_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            wait = _HOST_NEXT_ALLOWED.get(host, 0) - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            _HOST_NEXT_ALLOWED[host] = time.monotonic() + interval

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 2.0 * (2**attempt)) + random.uniform(0, 0.5)

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            return None

    def _robots_delay(self, host: str) -> float:
        cached = _ROBOTS_CACHE.get(host)
        if not cached or not isinstance(cached[1], urllib.robotparser.RobotFileParser):
            return 0.0
        delay = cached[1].crawl_delay(self.settings.discovery_robots_token)
        return min(float(delay), MAX_CRAWL_DELAY_SECONDS) if delay else 0.0

    async def _enforce_robots(self, url: str) -> None:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        policy = await self._robots_policy(parts.scheme, host)
        if policy == "DISALLOW_ALL":
            raise FetchError(
                "ROBOTS_DISALLOWED", "robots.txt was unreachable; access is not assumed.", url
            )
        if isinstance(policy, urllib.robotparser.RobotFileParser):
            token = self.settings.discovery_robots_token
            if not policy.can_fetch(token, url):
                raise FetchError("ROBOTS_DISALLOWED", "Disallowed by robots.txt.", url)
            delay = policy.crawl_delay(token)
            if delay and float(delay) > MAX_CRAWL_DELAY_SECONDS:
                raise FetchError(
                    "ROBOTS_DISALLOWED", f"robots.txt crawl-delay {delay}s is too long.", url
                )

    async def _robots_policy(self, scheme: str, host: str) -> Any:
        cached = _ROBOTS_CACHE.get(host)
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        try:
            response = await self.request(
                "GET",
                f"{scheme}://{host}/robots.txt",
                max_bytes=ROBOTS_MAX_BYTES,
                check_robots=False,
                count_request=False,
            )
        except FetchError as exc:
            # RFC 9309: an unavailable (4xx) robots.txt allows access; an unreachable one
            # (5xx or network failure) means complete disallow. 429 counts as unreachable.
            # A host that does not resolve at all is reported as such, not as a robots block.
            if exc.code == "UNSAFE_URL" or (
                exc.code == "NETWORK_ERROR" and str(exc.args[0]).startswith("DNS")
            ):
                raise
            if exc.status_code and 400 <= exc.status_code < 500 and exc.status_code != 429:
                policy: Any = "ALLOW_ALL"
                ttl: float = ROBOTS_TTL_SECONDS
            else:
                policy, ttl = "DISALLOW_ALL", ROBOTS_FAILURE_TTL_SECONDS
        else:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(response.text.splitlines())
            policy, ttl = parser, ROBOTS_TTL_SECONDS
        _ROBOTS_CACHE[host] = (now + ttl, policy)
        return policy


def reset_politeness_state() -> None:
    """Clear process-wide pacing, cooldown, and robots caches (used by tests)."""
    _HOST_NEXT_ALLOWED.clear()
    _HOST_COOLDOWN_UNTIL.clear()
    _HOST_LOCKS.clear()
    _ROBOTS_CACHE.clear()
