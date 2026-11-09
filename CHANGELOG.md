# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `mecha scan`: find AI keys in plaintext on this machine (files, AI tool configs, shell
  histories, notebooks), offline and on demand; severity by location, confidence by
  variable name, import of findings and an optional `--check` with each key's provider.
  Locations of keys Mecha keeps are stored encrypted and turn their card amber.

## [0.1.2] - 2026-11-09

### Added

- Language switch (EN/ES) next to the theme toggle, remembered in the browser; the
  browser's language is still the default. In the app and the web demo.
- Footer crediting the author, with links to YouTube, GitHub and his books.

## [0.1.1] - 2026-11-09

### Added

- Web demo on GitHub Pages (https://joaquinruiz.github.io/mecha/): the dashboard with
  made-up data, in the browser, with no install. Its data comes from demo mode, it never
  asks for a key ("Add" offers sample keys) and its CSP forbids any connection.
- README: a link to the web demo and to the launch post (in Spanish).

## [0.1.0] - 2026-11-09

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

[0.1.2]: https://github.com/JoaquinRuiz/mecha/releases/tag/v0.1.2
[0.1.1]: https://github.com/JoaquinRuiz/mecha/releases/tag/v0.1.1
[0.1.0]: https://github.com/JoaquinRuiz/mecha/releases/tag/v0.1.0
