# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in SocialCleaner, please **do not open a public issue**. Instead, use GitHub's private vulnerability reporting:

1. Go to the [Security tab](../../security/advisories/new) of this repository
2. Click **"Report a vulnerability"**
3. Describe the issue, steps to reproduce, and potential impact

You can expect an initial response within 72 hours.

## Scope

Security issues of particular concern for this project:

- **Cookie/session handling** — encrypted storage, exposure of decrypted values
- **Local API** — unauthorized access to the backend on `127.0.0.1`
- **Browser automation** — credential interception during the login flow
- **Dependency vulnerabilities** — in Python or Node packages

## Out of Scope

- Issues requiring physical access to the user's machine
- Social engineering attacks
- Vulnerabilities in Instagram/Twitter/X itself

## Supported Versions

Only the latest release is actively maintained.
