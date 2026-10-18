from __future__ import annotations

import os
import ssl
import threading
import time
import gzip
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from sensors.safe_http_transport import (
    PinnedHTTPXTransport,
    PinnedTarget,
    SafeStoreFetcher,
    TransportPolicyError,
    _public_address,
    validate_url,
)


def test_url_policy_and_idna():
    allow = frozenset({"xn--bcher-kva.example"})
    assert validate_url("https://bücher.example/item", allow).raw_host.decode("ascii") == "xn--bcher-kva.example"
    for url in (
        "http://xn--bcher-kva.example/", "https://xn--bcher-kva.example:444/",
        "https://user@xn--bcher-kva.example/", "https://other.example/",
        "https://xn--bcher-kva.example/#frag", "https://127.0.0.1/",
        "https://", "not-a-url", "https://xn--bcher-kva.example:bad/",
    ):
        with pytest.raises(TransportPolicyError):
            validate_url(url, allow)


@pytest.mark.parametrize("address", [
    "0.0.0.0", "10.0.0.1", "100.64.0.1", "127.0.0.1", "169.254.169.254",
    "172.16.0.1", "192.168.1.1", "192.0.2.5", "224.0.0.1", "::", "::1", "fe80::1",
    "fc00::1", "ff02::1", "2001:db8::1", "2002::1", "64:ff9b:1::1",
    "::ffff:127.0.0.1", "2001:0::1",
])
def test_rejects_non_global_and_mapped_addresses(address):
    with pytest.raises(TransportPolicyError):
        _public_address(address)


def test_mixed_answers_denied_before_transport_creation(monkeypatch):
    calls = []
    fetcher = SafeStoreFetcher(["shop.example"], resolver=lambda host: ["93.184.216.34", "127.0.0.1"])
    monkeypatch.setattr("sensors.safe_http_transport.PinnedHTTPXTransport", lambda *a, **k: calls.append(a))
    with pytest.raises(TransportPolicyError):
        fetcher.fetch("https://shop.example/")
    assert calls == []


def test_transport_rejects_disabled_tls_and_unbounded_timeouts():
    with pytest.raises(ValueError, match="TLS verification"):
        PinnedHTTPXTransport(PinnedTarget("shop.example", "93.184.216.34"), verify=False)
    with pytest.raises(ValueError, match="finite and positive"):
        PinnedHTTPXTransport(PinnedTarget("shop.example", "93.184.216.34"), timeout=float("inf"))


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, pin, router, calls):
        self.pin, self.router, self.calls = pin, router, calls

    def handle_request(self, request):
        self.calls.append((self.pin, str(request.url)))
        status, headers, body = self.router(request)
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(body), request=request)

    def close(self):
        pass


def _mock_fetcher(monkeypatch, router, resolver, *, hosts=("shop.example", "other.example"), **kwargs):
    connections = []
    monkeypatch.setattr(
        "sensors.safe_http_transport.PinnedHTTPXTransport",
        lambda pin, **_: _FakeTransport(pin, router, connections),
    )
    return SafeStoreFetcher(hosts, resolver=resolver, **kwargs), connections


def _html_response(_request):
    return 200, {"content-type": "text/html; charset=utf-8"}, b"<html>ok</html>"


def test_redirect_revalidates_and_resolves_each_hop(monkeypatch):
    resolved = []

    def resolver(host):
        resolved.append(host)
        return ["93.184.216.34"]

    def router(request):
        if request.url.host == "shop.example":
            return 302, {"location": "https://other.example/final"}, b""
        return _html_response(request)

    fetcher, connections = _mock_fetcher(monkeypatch, router, resolver)
    assert fetcher.fetch("https://shop.example/start") == b"<html>ok</html>"
    assert resolved == ["shop.example", "other.example"]
    assert [item[0].hostname for item in connections] == resolved


def test_dns_rebinding_answer_is_refreshed_and_pinned_per_hop(monkeypatch):
    answers = iter((["93.184.216.34"], ["1.1.1.1"]))
    resolver_calls = []

    def resolver(host):
        resolver_calls.append(host)
        return next(answers)

    def router(request):
        if request.url.path == "/start":
            return 302, {"location": "https://shop.example/next"}, b""
        return _html_response(request)

    fetcher, connections = _mock_fetcher(monkeypatch, router, resolver, hosts=("shop.example",))
    assert fetcher.fetch("https://shop.example/start") == b"<html>ok</html>"
    assert resolver_calls == ["shop.example", "shop.example"]
    assert [pin.address for pin, _ in connections] == ["93.184.216.34", "1.1.1.1"]


def test_denied_redirect_target_has_zero_connector_calls(monkeypatch):
    resolved = []

    def resolver(host):
        resolved.append(host)
        return ["93.184.216.34"] if host == "shop.example" else ["127.0.0.1"]

    fetcher, connections = _mock_fetcher(
        monkeypatch,
        lambda request: (302, {"location": "https://other.example/private"}, b""),
        resolver,
    )
    with pytest.raises(TransportPolicyError):
        fetcher.fetch("https://shop.example/start")
    assert resolved == ["shop.example", "other.example"]
    assert len(connections) == 1


def test_redirect_disallowed_host_loop_and_hop_limit(monkeypatch):
    fetcher, connections = _mock_fetcher(
        monkeypatch, lambda r: (302, {"location": "https://not-allowed.example/"}, b""),
        lambda _: ["93.184.216.34"],
    )
    with pytest.raises(TransportPolicyError):
        fetcher.fetch("https://shop.example/")
    assert len(connections) == 1

    fetcher, connections = _mock_fetcher(
        monkeypatch, lambda r: (302, {"location": str(r.url)}, b""),
        lambda _: ["93.184.216.34"],
    )
    with pytest.raises(TransportPolicyError, match="loop"):
        fetcher.fetch("https://shop.example/")
    assert len(connections) == 1

    fetcher, connections = _mock_fetcher(
        monkeypatch, lambda r: (302, {"location": str(r.url.copy_with(path=r.url.path + "x"))}, b""),
        lambda _: ["93.184.216.34"], max_redirects=1,
    )
    with pytest.raises(TransportPolicyError, match="hop limit"):
        fetcher.fetch("https://shop.example/a")
    assert len(connections) == 2


def test_status_content_type_and_decoded_size_limits(monkeypatch):
    for status, headers, body, expected in (
        (404, {"content-type": "text/html"}, b"no", "status"),
        (200, {"content-type": "application/json"}, b"{}", "HTML"),
        (200, {"content-type": "text/html"}, b"123456", "limit"),
    ):
        fetcher, _ = _mock_fetcher(
            monkeypatch, lambda _r, row=(status, headers, body): row,
            lambda _: ["93.184.216.34"], max_bytes=5,
        )
        with pytest.raises(TransportPolicyError, match=expected):
            fetcher.fetch("https://shop.example/")


def test_chunked_dishonest_length_and_compressed_expansion_are_counted(monkeypatch):
    rows = [
        (200, {"content-type": "text/html", "transfer-encoding": "chunked"}, b"123456"),
        (200, {"content-type": "text/html", "content-length": "1"}, b"123456"),
        (200, {"content-type": "text/html", "content-encoding": "gzip"}, gzip.compress(b"x" * 20)),
    ]
    for status, headers, body in rows:
        fetcher, _ = _mock_fetcher(
            monkeypatch, lambda _r, row=(status, headers, body): row,
            lambda _: ["93.184.216.34"], max_bytes=5,
        )
        with pytest.raises(TransportPolicyError, match="limit"):
            fetcher.fetch("https://shop.example/")


def test_proxy_environment_poisoning_does_not_change_direct_tls(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    test_real_tls_socket_pins_peer_and_verifies_original_hostname()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if getattr(self.server, "delay", 0):
            time.sleep(self.server.delay)
        self.server.received_host = self.headers.get("Host")
        self.server.received_local_ip = self.connection.getsockname()[0]
        body = getattr(self.server, "body", b"<html>ok</html>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


CERT_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "safe_transport")


def test_real_tls_socket_pins_peer_and_verifies_original_hostname():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(os.path.join(CERT_DIR, "server.pem"), os.path.join(CERT_DIR, "server.key"))
    server_context.sni_callback = lambda _sock, name, _ctx: setattr(server, "received_sni", name)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        context = ssl.create_default_context(cafile=os.path.join(CERT_DIR, "ca.pem"))
        transport = PinnedHTTPXTransport(PinnedTarget("shop.example", "127.0.0.1", server.server_port), verify=context)
        with httpx.Client(transport=transport, trust_env=False) as client:
            response = client.get(f"https://shop.example:{server.server_port}/")
        assert response.status_code == 200
        assert response.content == b"<html>ok</html>"
        assert server.received_host == f"shop.example:{server.server_port}"
        assert server.received_local_ip == "127.0.0.1"
        assert server.received_sni == "shop.example"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_wrong_hostname_rejected():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERT_DIR, "server.pem"), os.path.join(CERT_DIR, "server.key"))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        trusted = ssl.create_default_context(cafile=os.path.join(CERT_DIR, "ca.pem"))
        transport = PinnedHTTPXTransport(PinnedTarget("wrong.example", "127.0.0.1", server.server_port), verify=trusted)
        with httpx.Client(transport=transport, trust_env=False) as client, pytest.raises(httpx.ConnectError):
            client.get(f"https://wrong.example:{server.server_port}/")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_untrusted_ca_rejected():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERT_DIR, "server.pem"), os.path.join(CERT_DIR, "server.key"))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = PinnedHTTPXTransport(PinnedTarget("shop.example", "127.0.0.1", server.server_port))
        with httpx.Client(transport=transport, trust_env=False) as client, pytest.raises(httpx.ConnectError):
            client.get(f"https://shop.example:{server.server_port}/")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_local_tls_read_timeout():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.delay = 0.3
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERT_DIR, "server.pem"), os.path.join(CERT_DIR, "server.key"))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = PinnedHTTPXTransport(
            PinnedTarget("shop.example", "127.0.0.1", server.server_port),
            verify=ssl.create_default_context(cafile=os.path.join(CERT_DIR, "ca.pem")), timeout=0.05,
        )
        with httpx.Client(transport=transport, trust_env=False) as client, pytest.raises(httpx.ReadTimeout):
            client.get(f"https://shop.example:{server.server_port}/")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_local_tls_overall_deadline_and_wire_byte_limit():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.delay = 0.3
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERT_DIR, "server.pem"), os.path.join(CERT_DIR, "server.key"))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 0.05
        transport = PinnedHTTPXTransport(
            PinnedTarget("shop.example", "127.0.0.1", server.server_port, deadline),
            verify=ssl.create_default_context(cafile=os.path.join(CERT_DIR, "ca.pem")), timeout=1.0,
        )
        with httpx.Client(transport=transport, trust_env=False) as client, pytest.raises(httpx.ReadTimeout):
            client.get(f"https://shop.example:{server.server_port}/")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.body = b"123456"
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERT_DIR, "server.pem"), os.path.join(CERT_DIR, "server.key"))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        transport = PinnedHTTPXTransport(
            PinnedTarget("shop.example", "127.0.0.1", server.server_port),
            verify=ssl.create_default_context(cafile=os.path.join(CERT_DIR, "ca.pem")),
            max_wire_bytes=5,
        )
        with httpx.Client(transport=transport, trust_env=False) as client, pytest.raises(TransportPolicyError, match="wire"):
            client.get(f"https://shop.example:{server.server_port}/")
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
