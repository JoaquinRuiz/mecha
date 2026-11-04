# Mecha

> How much fuse is left on your API keys?

Mecha is a local-first dashboard (web + CLI) that tells you at a glance how much life each of your AI credentials has left: whether it works, whether it has funds, when it expires and what kind of key it is. It covers Anthropic, OpenAI, Google Gemini and xAI, with OpenRouter planned.

**Status:** early development. See [SPEC.md](SPEC.md) (in Spanish) for the full design and the phased plan.

## Principles

- **Local-first.** Runs on `127.0.0.1`. A key only ever leaves your machine towards its own provider's official endpoint. No telemetry, no cloud.
- **Zero cost by default.** Automatic checks only use free endpoints; token-consuming probes are opt-in.
- **Honest about the unknown.** "Unknown" is a valid state, and every value shows where it came from.
- **A stored secret is never shown again.** The UI only sees a redacted form and a fingerprint.

## License

[MIT](LICENSE)
