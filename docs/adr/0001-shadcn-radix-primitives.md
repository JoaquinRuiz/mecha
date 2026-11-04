# ADR 0001: shadcn/ui on Radix primitives

- Status: accepted
- Date: 2026-11-04
- Phase: 0

## Context

SPEC.md asks for shadcn/ui with "Radix primitives, the default ones" and marks the claim as
needing verification. A third-party article (PkgPulse) suggested that in 2026 the shadcn CLI
might default to Base UI instead.

What the official docs say (checked 2026-11-04):

- [CLI reference](https://ui.shadcn.com/docs/cli): `shadcn init` accepts
  `-b, --base <base>` with values `base`, `radix` and `aria`. **No default is documented.**
  `--defaults` is documented as `--template=next --preset=nova` and says nothing about the base.
- [Vite installation](https://ui.shadcn.com/docs/installation/vite) only says `init` prompts
  for "base, preset, monorepo, and more".

So "Radix is the default" cannot be confirmed; the choice depends on the prompt or flag.

## Decision

Pin Radix explicitly and never depend on the CLI default:

```bash
pnpm dlx shadcn@latest init --base radix --preset nova --template vite
```

`web/components.json` records `"style": "radix-nova"`, so later `shadcn add` calls pull the
Radix variants. Components import primitives from the unified `radix-ui` package.

## Consequences

- Behaviour matches the spec regardless of future CLI defaults.
- If the project ever moves to Base UI it is a deliberate change with its own ADR, re-running
  `init --base base` and regenerating `src/components/ui/`.
- The generated `cn()` helper comes from the [`cn`](https://github.com/shadcn-ui/cn) package
  (published by the shadcn-ui organisation, replaces clsx + tailwind-merge). Verified as an
  official shadcn-ui repository before accepting the dependency.
- Open question for phase 3: some Radix components (e.g. `Dialog` via `react-remove-scroll`)
  inject `<style>` elements at runtime, which the strict `style-src 'self'` CSP would block.
  Check when the first such component lands, and prefer a per-response nonce over
  `'unsafe-inline'` if needed.
