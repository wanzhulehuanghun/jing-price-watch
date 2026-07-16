# Security

## Scope

JingPriceWatch is a local-only application. The server rejects non-loopback bind addresses and must not be exposed through a reverse proxy or port forward.

## URL and session safety

- Only public `http://` and `https://` product URLs on standard ports are accepted.
- Loopback, private, link-local, reserved, credential-bearing, and non-web URLs are rejected.
- Browser requests are intercepted and private-network destinations are blocked before they are continued.
- Webhook destinations receive the same public-network validation and redirects are rejected.
- Each shopping site uses an isolated Edge profile. Cookies and passwords are never returned by the API or written to the database.
- Generic remote product images are not reloaded by the dashboard.

The local user must still treat submitted links as untrusted and should only log in to shopping sites they recognize. DNS rebinding and browser vulnerabilities cannot be completely eliminated by an application-level filter.

## Reporting a vulnerability

Please open a GitHub security advisory or a private maintainer report. Do not include cookies, passwords, database files, or full browser profiles in reports.
