# Mecha

> How much fuse is left on your API keys?

Mecha is a local-first dashboard (web + CLI) that tells you at a glance how much life each of your AI credentials has left: whether it works, whether it has funds, when it expires and what kind of key it is. It covers Anthropic, OpenAI, Google Gemini and xAI, with OpenRouter planned.

**Status:** early development. See [SPEC.md](SPEC.md) (in Spanish) for the full design and the phased plan.

## Principles

- **Local-first.** Runs on `127.0.0.1`. A key only ever leaves your machine towards its own provider's official endpoint. No telemetry, no cloud.
- **Zero cost by default.** Automatic checks only use free endpoints; token-consuming probes are opt-in.
- **Honest about the unknown.** "Unknown" is a valid state, and every value shows where it came from.
- **A stored secret is never shown again.** The UI only sees a redacted form and a fingerprint.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22+ with [pnpm](https://pnpm.io/) (version pinned in `web/package.json`), [just](https://just.systems/) and, for the pre-commit hook, [gitleaks](https://github.com/gitleaks/gitleaks).

```bash
just setup    # uv sync, pnpm install, gitleaks pre-commit hook
just dev      # FastAPI on 127.0.0.1:7878 + Vite on localhost:5173 (opens the Vite URL)
just test     # pytest + vitest
just lint     # ruff, mypy, biome, tsc
just build    # vite build -> src/mecha/static, then uv build (wheel includes the UI)
uv run mecha serve   # serve the built UI on http://127.0.0.1:7878
```

Each server start generates a random session token and opens `http://127.0.0.1:7878/#t=<token>`. The page keeps the token in `sessionStorage` (so reloads work) and sends it as `Authorization: Bearer`. Every `/api/*` request without it gets `401`, and any `Host` other than `127.0.0.1:<port>` or `localhost:<port>` gets `400`.

Repository layout:

```text
src/mecha/      Python package (CLI, FastAPI server, core, providers, store)
web/            Vite + React + TypeScript + Tailwind CSS v4 + shadcn/ui (Radix)
tests/          pytest
docs/           provider notes and architecture decision records (docs/adr/)
```

Never use real API keys in tests, fixtures or commits.

## License

[MIT](LICENSE)
