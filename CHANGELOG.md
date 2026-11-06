# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - unreleased

First public release (alpha).

### Added

- Encrypted local store (AES-256-GCM; key in the system keyring or wrapped with a
  passphrase) and offline detection of Anthropic, OpenAI, Gemini, xAI and OpenRouter keys.
- Free passive checks against each provider's official endpoints, with a closed list of
  method, host and path per kind of key; traffic-light state with the source of every value.
- Web dashboard on `127.0.0.1` (session token, `Host` check, CSP with nonces) and CLI with
  Nagios exit codes; demo mode with fake data.
- Admin and management keys: expiry, last use and status of their keys; xAI team balance;
  OpenAI estimated organization balance.
- Opt-in paid active probes with their cost shown first, at most one per hour per key.
- Read-only linking of Codex, Claude Code and Gemini CLI session files; `.env` import with
  a redacted preview.
- `mecha export` / `mecha restore`: encrypted backups with their own passphrase.

[0.1.0]: https://github.com/JoaquinRuiz/mecha/releases/tag/v0.1.0
