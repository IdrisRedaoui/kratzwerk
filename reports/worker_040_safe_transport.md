# Worker 040 — Safe HTTPX Transport

## Final status: `BLOCKED_RUNTIME_OR_DEPENDENCY`

The connector gate passed. A narrow adapter now uses HTTPX 0.28.1's public `BaseTransport` boundary with HTTP Core's documented `ConnectionPool`, `NetworkBackend`, and `NetworkStream` interfaces. The backend receives the original hostname from HTTP Core but sends the TCP dial to a previously validated numeric address. HTTP Core keeps the origin hostname for SNI and certificate hostname verification. The implementation does not rewrite the URL to an IP, disable CA verification, monkey-patch HTTPX, or fall back to hostname resolution.

The current repository runtime is CPython 3.11.0 with OpenSSL 1.1.1q. Python 3.11.0 is superseded by 3.11.17; Python 3.11 remains in security-fix-only support through October 2027. OpenSSL 1.1.1 reached public end of life on 11 September 2023. The deployment runtime is not separately identified, and this local OpenSSL build is past public support. Live use is therefore blocked until the target runtime and its patched Python/OpenSSL builds are confirmed. This report does not claim production readiness.

## Connector design and API stability

- **Public, supported:** HTTPX `BaseTransport`; HTTP Core `ConnectionPool`, `NetworkBackend`, `NetworkStream`, `SyncBackend`; HTTP Core's documented `connect_tcp` and `start_tls` flow. The backend connects to the validated IP and HTTP Core applies TLS using the request origin hostname.
- **Private or version-sensitive:** None intentionally used. The adapter does not access HTTPX's internal pool, request handling internals, or monkey-patch methods.
- **Version pins:** HTTPX 0.28.1 with HTTP Core 1.0.9, plus exact transitive dependencies and SHA-256 artifact hashes in `requirements.lock`. HTTP Core 1.0.9 is selected instead of 1.0.7 because its release notes identify a fix for GHSA-vqfr-h8mv-ghfj. Test dependencies and hashes are in `requirements-test.lock`.
- **Policy:** exact IDNA-canonicalized host allowlist, HTTPS/443 only in `SafeStoreFetcher`, no credentials or fragments, all DNS answers checked before dialing, fail closed on empty/mixed/prohibited answers, one validated numeric address pinned per hop, redirects are manual and bounded, environment proxies disabled, finite connect/read/write/pool timeouts, decoded response-byte cap, successful status and HTML media type required.
- **TLS:** CA verification and hostname checking must remain enabled. A caller cannot pass `verify=False`; custom `SSLContext` objects are rejected unless they require certificates and check hostnames.

The default resolver asks the operating system for all stream A/AAAA results. The adapter rejects non-global, multicast, reserved, and IPv4-mapped IPv6 addresses. It pins one validated answer for the request and does not retry through normal hostname resolution. IPv6 transition and special-purpose registry coverage still needs an explicit exhaustive policy review before deployment.

## Verification evidence

Command: `uv run --no-project --with-requirements requirements-test.lock python -m pytest tests/test_safe_http_transport.py -q`

Result: **34 passed**.

| Evidence type | What passed | Assurance boundary |
|---|---|---|
| Local-socket TLS integration | A local HTTPS server with a test CA and `shop.example` certificate accepted a request to the hostname while the backend dialed `127.0.0.1`; server observed the original Host header and local socket endpoint. The test hostname is not resolvable in public DNS, so success also demonstrates that the connector used the pinned IP instead of resolving the URL hostname. | Demonstrates this tested adapter and stack on this Windows host. It is not production egress isolation evidence. |
| Local-socket TLS rejection and limits | A certificate for the wrong hostname and an untrusted certificate chain each failed. Delayed local HTTPS responses triggered read timeout and the transport-level overall deadline. A local response exceeding the raw wire-byte cap was rejected. | Local socket behavior only; no merchant contacted. |
| Mock/policy tests | URL scheme, port, credentials, allowlist, IDNA, prohibited IPv4/IPv6 and mapped IPv6; mixed DNS denial before transport construction; per-hop resolution, denied/disallowed redirects, redirect loops/hop limit; proxy environment poisoning; unsuccessful status, non-HTML, oversized decoded body, dishonest Content-Length, chunked-size accounting, and compressed expansion. | Mock assertions establish policy dispatch behavior, not that the operating system or deployment firewall blocks egress. The rejected redirect target zero-connector assertion is mocked. |

Tests do **not** establish production egress isolation. Required deployment controls are a network policy/firewall denying private, loopback, link-local, multicast, reserved, and cloud metadata destinations for both IPv4 and IPv6. A production egress review must verify those controls independently.

Remaining verification gaps include an actual local-socket redirect-to-denied-target counter, deterministic connect-timeout coverage, end-to-end overall-deadline coverage including DNS, IPv6 socket integration, and broader special-purpose/transition IPv6 range tests. Network reads/writes/connect/TLS operations are clamped to the absolute deadline, but synchronous OS name resolution is not bounded by it. These are recorded limitations; the passing test count must not be read as covering every requested scenario.

## Runtime and dependencies

- Observed runtime: CPython 3.11.0, Windows x64, OpenSSL 1.1.1q (5 July 2022).
- Python 3.11 remains supported for security fixes until October 2027, but the installed 3.11.0 is far behind the latest 3.11.17 security release. OpenSSL 1.1.1 public support ended in 2023. The exact production target is unresolved; use a currently patched Python/OpenSSL build and verify its vendor support before enabling live transport.
- Runtime licenses reviewed: HTTPX and HTTP Core BSD-3-Clause; AnyIO MIT; certifi MPL-2.0; h11 MIT; idna BSD; sniffio MIT; typing-extensions PSF-2.0. Test-only pytest stack is separately pinned with hashes.
- `requirements.txt` / `requirements-test.txt` are reviewable inputs; `requirements.lock` / `requirements-test.lock` pin all resolved packages and accepted SHA-256 hashes. Install with hash enforcement, for example `pip install --require-hashes -r requirements.lock`.

## Database and scope controls

Read-only SHA-256 measurement of `data/Database.sqlite`: `5ca539eb7a03d84aad54b79c353961e78efc594df54eb6aa13410cf574aa0aff`. No expected baseline hash was supplied, so this records the observed production database artifact; it is not a comparison to an external known-good value. No database write, migration, capture, recovery, restore, rollback, or product persistence was performed.

No live merchant site was contacted. Migrations 001–004 were not modified. Worker 037 files were not modified or staged. No subsequent worker was started.

## Exact changed files

- `sensors/safe_http_transport.py`
- `tests/test_safe_http_transport.py`
- `tests/fixtures/safe_transport/ca.pem`
- `tests/fixtures/safe_transport/server.pem`
- `tests/fixtures/safe_transport/server.key` (synthetic test-only key)
- `requirements.txt`
- `requirements.lock`
- `requirements-test.txt`
- `requirements-test.lock`
- `reports/worker_040_safe_transport.md`
- `reports/worker_040_safe_transport.json`

## API and platform references

- [HTTPX 0.28.1 transport implementation](https://raw.githubusercontent.com/encode/httpx/0.28.1/httpx/_transports/default.py)
- [HTTPX custom transports](https://www.python-httpx.org/advanced/transports/)
- [HTTP Core network backends](https://www.encode.io/httpcore/network-backends/)
- [HTTP Core extensions: SNI hostname](https://www.encode.io/httpcore/extensions/)
- [HTTP Core 1.0.9 release notes](https://pypi.org/project/httpcore/)
- [Python 3.11.17 support status](https://www.python.org/downloads/release/python-31117/)
- [OpenSSL 1.1.1 end of life](https://mirror.openssl-library.org/post/2023-06-15-1.1.1-eol-reminder/)
