# New Source Loopback Proxy Design

## Scope

Allow the controlled point-in-time collector to reach the newly configured
market-data source through the user's local HTTP proxy. This changes only the
network route. It does not change endpoints, request fields, response parsing,
row caps, coverage rules, artifact schemas, strategy logic, or OOS isolation.

## Security boundary

- Proxy use is opt-in for the new source profile only.
- Accept only an `http` proxy whose resolved host is the loopback interface:
  `127.0.0.1`, `::1`, or `localhost`.
- Require an explicit valid TCP port.
- Reject credentials, paths other than `/`, query strings, fragments, remote
  hosts, non-HTTP schemes, and malformed values.
- Continue to pin the upstream HTTPS hostname and reject redirects.
- Read the source token only from its existing environment variable. Never
  persist or log it.

## Components and data flow

The source resolver validates the proxy URL and returns an immutable proxy
route descriptor. The job entry point passes that descriptor into the
collector transport. The transport installs a proxy handler only for HTTPS
requests and otherwise retains the existing no-proxy behavior.

Immutable request semantics record the route mode (`direct` or
`loopback_http_proxy`) and the non-secret proxy endpoint. Raw response bytes,
request hashes, timestamps, row-cap checks, and artifact lineage remain
unchanged.

## Failure behavior

Invalid proxy configuration fails before any request is sent. Connection,
TLS, response, schema, completeness, and PIT reconciliation failures retain
their existing retry and fail-closed behavior. Resuming a partially collected
store reuses verified receipts and fetches only missing partitions.

## Tests and acceptance

Tests must first demonstrate that the current transport cannot use the local
route. The implementation is accepted only when tests prove:

1. a loopback proxy is wired only for the new source;
2. remote, credentialed, malformed, and non-HTTP proxies are rejected;
3. direct mode remains proxy-free;
4. request semantics bind the selected network route without exposing tokens;
5. the existing collector, adversarial, full pytest, ruff, and diff checks pass;
6. the preregistered four-session real collection resumes, audits, publishes,
   and reopens offline without strategy or final-OOS execution.
