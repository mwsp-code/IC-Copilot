# Security Policy

## Secrets

Never report API keys in a public issue. The app stores saved credentials through the OS keychain or
a Windows DPAPI-encrypted local fallback under the gitignored `data/` directory. Environment files,
local databases, logs, and Streamlit secrets are excluded from version control.

If a credential is exposed in a commit, screenshot, issue, or chat, revoke and rotate it immediately.
Removing it from a later commit does not remove it from Git history.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature when available. Include the affected
version, impact, reproduction steps, and a minimal proof of concept. Do not include real financial-data
vendor payloads or credentials.

This research application is not an execution or custody system, but security issues affecting secret
handling, source integrity, citation provenance, or local API exposure are treated as high priority.
