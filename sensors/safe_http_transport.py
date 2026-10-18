"""Policy-enforcing HTTPX transport for pinned, allowlisted HTTPS origins.

This adapter deliberately uses only HTTPX's public BaseTransport API and
HTTP Core's documented ConnectionPool/NetworkBackend interfaces.
"""
from __future__ import annotations

import ipaddress
import math
import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urljoin

import httpcore
import httpx


_SPECIAL_V4 = tuple(ipaddress.ip_network(value) for value in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
    "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
_SPECIAL_V6 = tuple(ipaddress.ip_network(value) for value in (
    "::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "100::/64", "2001::/23", "2001:db8::/32", "2002::/16", "3fff::/20",
    "fc00::/7", "fe80::/10", "ff00::/8",
))


class TransportPolicyError(ValueError):
    """The request violates the safe-fetch policy."""


@dataclass(frozen=True)
class PinnedTarget:
    hostname: str
    address: str
    port: int = 443
    deadline: float | None = None


def _canonical_hostname(hostname: str) -> str:
    try:
        return hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise TransportPolicyError("invalid IDNA hostname") from exc


def _public_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise TransportPolicyError("malformed address") from exc
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped or address.sixtofour or address.teredo:
            raise TransportPolicyError("mapped and transition IPv6 forms are not allowed")
        if any(address in network for network in _SPECIAL_V6):
            raise TransportPolicyError("special-purpose IPv6 address is not allowed")
    elif any(address in network for network in _SPECIAL_V4):
        raise TransportPolicyError("special-purpose IPv4 address is not allowed")
    if not address.is_global or address.is_multicast or address.is_reserved:
        raise TransportPolicyError("destination address is not globally routable")
    return address


def validate_url(url: str | httpx.URL, allowlist: frozenset[str]) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError) as exc:
        raise TransportPolicyError("malformed URL") from exc
    if parsed.scheme != "https" or parsed.port not in (None, 443):
        raise TransportPolicyError("only HTTPS on port 443 is allowed")
    if parsed.username or parsed.password or not parsed.raw_host or parsed.fragment:
        raise TransportPolicyError("credentials, empty host, and fragments are forbidden")
    raw_hostname = parsed.raw_host.decode("ascii")
    host = _canonical_hostname(raw_hostname.strip("[]"))
    if host not in allowlist:
        raise TransportPolicyError("hostname is not allowlisted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise TransportPolicyError("IP-literal URLs are not allowed")
    return parsed.copy_with(host=host)


def resolve_public(hostname: str) -> tuple[str, ...]:
    """Resolve every A/AAAA result and reject the entire set if any is unsafe."""
    records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    if not records:
        raise TransportPolicyError("hostname resolved to no addresses")
    addresses = tuple(dict.fromkeys(record[4][0] for record in records))
    for address in addresses:
        _public_address(address)
    return addresses


class _PinnedBackend(httpcore.NetworkBackend):
    def __init__(self, pin: PinnedTarget):
        self.pin = pin
        self.backend = httpcore.SyncBackend()

    def _remaining(self, timeout, timeout_error):
        if self.pin.deadline is None:
            return timeout
        remaining = self.pin.deadline - time.monotonic()
        if remaining <= 0:
            raise timeout_error("overall deadline exceeded")
        return remaining if timeout is None else min(timeout, remaining)

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        if host.lower().rstrip(".") != self.pin.hostname or port != self.pin.port:
            raise TransportPolicyError("connector received an unvalidated origin")
        stream = self.backend.connect_tcp(
            self.pin.address, port, timeout=self._remaining(timeout, httpcore.ConnectTimeout), local_address=local_address,
            socket_options=socket_options,
        )
        return _DeadlineStream(stream, self.pin.deadline)

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise TransportPolicyError("Unix sockets are not permitted")


class _DeadlineStream(httpcore.NetworkStream):
    def __init__(self, stream, deadline):
        self.stream = stream
        self.deadline = deadline

    def _timeout(self, timeout, exception_type):
        if self.deadline is None:
            return timeout
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise exception_type("overall deadline exceeded")
        return remaining if timeout is None else min(timeout, remaining)

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout=self._timeout(timeout, httpcore.ReadTimeout))

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout=self._timeout(timeout, httpcore.WriteTimeout))

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.stream = self.stream.start_tls(
            ssl_context, server_hostname=server_hostname,
            timeout=self._timeout(timeout, httpcore.ConnectTimeout),
        )
        return self

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)


class _ResponseStream(httpx.SyncByteStream):
    def __init__(self, stream, request, max_wire_bytes):
        self.stream = stream
        self.request = request
        self.max_wire_bytes = max_wire_bytes

    def __iter__(self):
        try:
            total = 0
            for part in self.stream:
                total += len(part)
                if total > self.max_wire_bytes:
                    raise TransportPolicyError("wire response byte limit exceeded")
                yield part
        except httpcore.ReadTimeout as exc:
            raise httpx.ReadTimeout(str(exc), request=self.request) from exc
        except httpcore.ReadError as exc:
            raise httpx.ReadError(str(exc), request=self.request) from exc
        except httpcore.RemoteProtocolError as exc:
            raise httpx.RemoteProtocolError(str(exc), request=self.request) from exc
        except httpcore.ProtocolError as exc:
            raise httpx.ProtocolError(str(exc), request=self.request) from exc

    def close(self):
        self.stream.close()


class PinnedHTTPXTransport(httpx.BaseTransport):
    """One request at a time, with a fresh DNS check and pinned connection."""

    def __init__(self, pin: PinnedTarget, *, verify=True, timeout=5.0, max_wire_bytes=2_000_000):
        self.pin = pin
        if not math.isfinite(timeout) or timeout <= 0 or max_wire_bytes <= 0:
            raise ValueError("timeouts and response byte limits must be finite and positive")
        if pin.deadline is not None and not math.isfinite(pin.deadline):
            raise ValueError("deadline must be finite")
        if verify is not True and not isinstance(verify, ssl.SSLContext):
            raise ValueError("TLS verification cannot be disabled")
        context = verify if isinstance(verify, ssl.SSLContext) else ssl.create_default_context()
        if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
            raise ValueError("TLS context must verify CA chains and hostnames")
        self.pool = httpcore.ConnectionPool(
            ssl_context=context,
            network_backend=_PinnedBackend(pin), max_connections=1,
            max_keepalive_connections=0, retries=0,
        )
        self.timeout = timeout
        self.max_wire_bytes = max_wire_bytes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request_host = request.url.raw_host.decode("ascii").lower().rstrip(".")
        if request_host != self.pin.hostname or request.url.port != self.pin.port:
            raise TransportPolicyError("request origin does not match pin")
        core_request = httpcore.Request(
            method=request.method, url=httpcore.URL(
                scheme=request.url.raw_scheme, host=request.url.raw_host,
                port=request.url.port, target=request.url.raw_path,
            ), headers=request.headers.raw, content=request.stream,
            extensions={"timeout": {"connect": self.timeout, "read": self.timeout,
                                    "write": self.timeout, "pool": self.timeout}},
        )
        try:
            response = self.pool.handle_request(core_request)
        except httpcore.ConnectError as exc:
            raise httpx.ConnectError(str(exc), request=request) from exc
        except httpcore.ConnectTimeout as exc:
            raise httpx.ConnectTimeout(str(exc), request=request) from exc
        except httpcore.ReadTimeout as exc:
            raise httpx.ReadTimeout(str(exc), request=request) from exc
        except httpcore.WriteTimeout as exc:
            raise httpx.WriteTimeout(str(exc), request=request) from exc
        except httpcore.PoolTimeout as exc:
            raise httpx.PoolTimeout(str(exc), request=request) from exc
        except httpcore.ReadError as exc:
            raise httpx.ReadError(str(exc), request=request) from exc
        except httpcore.WriteError as exc:
            raise httpx.WriteError(str(exc), request=request) from exc
        except httpcore.RemoteProtocolError as exc:
            raise httpx.RemoteProtocolError(str(exc), request=request) from exc
        except httpcore.ProtocolError as exc:
            raise httpx.ProtocolError(str(exc), request=request) from exc
        except httpcore.UnsupportedProtocol as exc:
            raise httpx.UnsupportedProtocol(str(exc), request=request) from exc
        return httpx.Response(response.status, headers=response.headers,
                              stream=_ResponseStream(response.stream, request, self.max_wire_bytes), request=request)

    def close(self):
        self.pool.close()


class SafeStoreFetcher:
    """Manual-redirect HTTPS fetcher. The parser remains outside this adapter."""

    def __init__(self, allowed_hosts, *, resolver=resolve_public, max_redirects=5,
                 max_bytes=2_000_000, timeout=5.0, deadline=15.0):
        if (not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(deadline)
                or deadline <= 0 or max_bytes <= 0 or max_redirects < 0):
            raise ValueError("timeouts and byte limits must be positive; redirect count must be nonnegative")
        self.allowed_hosts = frozenset(_canonical_hostname(h) for h in allowed_hosts)
        if not self.allowed_hosts:
            raise ValueError("at least one exact hostname is required")
        self.resolver = resolver
        self.max_redirects = max_redirects
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.deadline = deadline

    def fetch(self, url: str) -> bytes:
        expires = time.monotonic() + self.deadline
        visited: set[str] = set()
        current = url
        for hop in range(self.max_redirects + 1):
            parsed = validate_url(current, self.allowed_hosts)
            normalized = str(parsed)
            if normalized in visited:
                raise TransportPolicyError("redirect loop")
            visited.add(normalized)
            host = _canonical_hostname(parsed.raw_host.decode("ascii").strip("[]"))
            answers = tuple(self.resolver(host))
            if not answers:
                raise TransportPolicyError("hostname resolved to no addresses")
            for answer in answers:
                _public_address(answer)
            remaining = expires - time.monotonic()
            if remaining <= 0:
                raise httpx.TimeoutException("overall deadline exceeded")
            ip = answers[0]
            transport = PinnedHTTPXTransport(
                PinnedTarget(host, ip, deadline=expires), timeout=min(self.timeout, remaining),
                max_wire_bytes=self.max_bytes,
            )
            try:
                with httpx.Client(transport=transport, trust_env=False,
                                  follow_redirects=False, timeout=min(self.timeout, remaining)) as client:
                    with client.stream("GET", parsed, headers={"Accept": "text/html"}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location or hop >= self.max_redirects:
                                raise TransportPolicyError("redirect missing or hop limit exceeded")
                            current = urljoin(normalized, location)
                            continue
                        if not 200 <= response.status_code < 300:
                            raise TransportPolicyError(f"unsuccessful HTTP status {response.status_code}")
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if content_type not in ("text/html", "application/xhtml+xml"):
                            raise TransportPolicyError("response is not HTML")
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.max_bytes or time.monotonic() > expires:
                                raise TransportPolicyError("response limit or deadline exceeded")
                            chunks.append(chunk)
                        return b"".join(chunks)
            finally:
                transport.close()
        raise TransportPolicyError("redirect hop limit exceeded")
