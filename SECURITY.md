# 🔒 Security Policy

## 🐛 Reporting a vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Report vulnerabilities by emailing the maintainer directly (see the GitHub profile for contact details). Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact

You will receive a response within 7 days. If the issue is confirmed, a fix will be prioritised and a new release published.

## 🎯 Scope

This project is intended for **self-hosted, private network use**. It is not designed for public internet exposure without additional hardening (authentication, rate limiting, TLS termination via a reverse proxy).

Known limitations:
- No authentication on the Gradio UI or REST API
- `PATCH /api/config` accepts any caller — restrict network access if deploying on a shared host
- Google Translate backend uses an unofficial endpoint; traffic is not end-to-end encrypted by this application
