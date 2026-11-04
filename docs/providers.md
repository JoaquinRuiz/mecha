# Provider notes

Provider data that SPEC.md marks as «verificar» and that could not be confirmed in the
official documentation, or that differs from it. Each entry says what the spec claims,
what the docs say, the date checked and what Mecha does meanwhile (usually `unknown`).

Only provider data belongs here; architecture decisions live in [`adr/`](adr/).

## Key prefixes (phase 1, checked 2026-11-04)

`verified` in `src/mecha/data/detect_patterns.toml` is `true` only when the prefix appears in
the provider's official documentation. Unverified patterns still work, but the CLI/UI say
«format not confirmed in official docs».

| Pattern | Prefix / format | Official docs | `verified` | Mecha behaviour |
| --- | --- | --- | --- | --- |
| `anthropic_api_key` | `sk-ant-api03-` | Yes: `partial_key_hint` example `sk-ant-api03-R2D...igAA` in [List API Keys](https://platform.claude.com/docs/en/api/admin/api_keys/list) | true | — |
| `anthropic_admin_key` | `sk-ant-admin01-` | Not found in the Admin API pages | false | Detected as admin key |
| `anthropic_oauth_access` | `sk-ant-oat01-` | Undocumented (third-party sources only) | false | `oauth_token`, local inspection only |
| `anthropic_oauth_refresh` | `sk-ant-ort01-` | Undocumented (third-party sources only). SPEC: «verificar» | false | **Never stored**; the user is told it looks like a refresh token. Safe even if the prefix changes |
| `openai_project_key` | `sk-proj-` | Not found in the [Admin API reference](https://developers.openai.com/api/reference/resources/admin/subresources/organization.md) | false | Detected as project key |
| `openai_service_account_key` | `sk-svcacct-` | Not found in the Admin API reference | false | Detected as service account key |
| `openai_admin_key` | `sk-admin-` | Yes: examples `sk-admin-1234abcd` in the Admin API reference | true | — |
| `openai_legacy_key` | `sk-` (rest) | n/a (ambiguous by design) | false | User must confirm the provider |
| `gemini_auth_key` | `AQ.` | [API keys page](https://aistudio.google.com/docs/api-key) describes auth keys but not their format | false | Detected as auth key |
| `gemini_standard_key` | `AIza` + 35 | Same page: no format documented. It does confirm: since 2026-05-28 AI Studio only creates auth keys; unrestricted standard keys are rejected; the API «will move from standard keys to auth keys» (no date). The September 2026 cutoff comes from third parties only | false | Always amber («key type being retired») |
| `xai_api_key` | `xai-` | Yes: examples in [Management API, auth](https://docs.x.ai/developers/rest-api-reference/management/auth.md) | true | — |
| `openrouter_api_key` | `sk-or-v1-` | Yes: masked labels `sk-or-v1-au7...890` in [Get current API key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key.md) | true | — |

xAI management keys have no documented prefix: they are added with
`--provider xai --kind management_key`.

## Notes for phase 4 (hint matching)

- Anthropic `partial_key_hint` uses three ASCII dots: `sk-ant-api03-R2D...igAA`.
- OpenAI `redacted_value` examples: `sk-abc...def`, `sk-admin...def`.
- Matching generates each provider's hint format from the decrypted secret; it never
  compares against Mecha's own `redacted` form.
