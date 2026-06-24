# Contributing

Thanks for considering a contribution to Agentic Target Evidence. This project follows the
[Code of Conduct](CODE_OF_CONDUCT.md) — please read it before participating.

Before diving in, read [docs/developers.md](docs/developers.md): it covers project
conventions, the extension points (new source / lens / provider / capability), and the
"don't build on this" list of off-path code. This file covers the mechanics of submitting a
change; that one covers the design.

---

## Getting set up

```bash
uv sync                  # Python ≥ 3.12, via uv: https://docs.astral.sh/uv/
cp .env.example .env
make up                   # infra + Langfuse + OTEL + the app, as containers
```

See [docs/tutorial.md](docs/tutorial.md) for a full walkthrough, and
[docs/restricted.md](docs/restricted.md) if you need a gated source (OMIM, SCImago, GBD,
TTD) enabled locally.

---

## Before you open a PR

- **Discuss non-trivial changes first.** Open an issue or reach out (see
  [NOTICE.md](NOTICE.md#contact)) before starting work on a new data source, lens, or
  architectural change — it's much easier to align on approach before code is written than
  after.
- **Keep PRs scoped.** One source, one lens, one bug fix per PR. Unrelated cleanup belongs in
  its own PR.

## Code style and checks

```bash
uv run ruff check src/ tests/      # lint
uv run ruff format src/ tests/     # format
uv run mypy src/                   # strict type checking
make test                          # unit + contract tests (excludes integration/smoke)
```

All four must pass before a PR is merged; CI runs them on every push. Notes:

- `ruff` line length is 100; the selected rule sets are `E,F,I,UP,B,SIM,TCH`.
- `mypy --strict` with the Pydantic plugin — new code must be fully typed.
- `make test-schemas` runs schema/contract tests only; `make test-smoke` runs the full
  end-to-end pipeline (needs Ollama + internet) and is not required for most PRs.
- If you touch an `AgentContract` or `ServiceContract`, make sure the `consumes`/`produces`
  keys still match what the harness enforces at runtime.

## Commit messages

Commits must follow [Conventional Commits](https://www.conventionalcommits.org/) — this is
enforced by commitlint on every PR (`.commitlintrc.json`) and drives automated versioning via
Python Semantic Release on `main`. Allowed types: `feat`, `fix`, `perf`, `refactor`, `revert`,
`test`, `docs`, `build`, `ci`, `chore`, `style`.

```
feat(lenses): add regulatory_lens fast-track signal
fix(retrieval): handle empty ClinicalTrials.gov response
docs: clarify OMIM gating in restricted.md
```

Use a scope when it clarifies where the change lives (matching the directory or component,
e.g. `commercial-lens`, `mcp-gateway`, `workflow`).

## Adding a data source or lens

These have multi-step conventions (contract wiring, evidence-type routing, NOTICE.md
licensing entries) — follow [docs/developers.md § Extension points](docs/developers.md#extension-points)
rather than improvising. In particular:

- **Always update [NOTICE.md](NOTICE.md)** with the new source's license. If it's
  non-commercial, gate it behind a `<SOURCE>_ENABLED` flag, default off — see existing
  examples (OMIM, SCImago, GBD, TTD).
- **Always update [docs/data_sources.md](docs/data_sources.md)** to match.

## Tests

Mirror `src/` under `tests/`. New code needs tests; bug fixes should include a regression
test that fails before the fix and passes after.

## Submitting

1. Fork the repo and branch from `main`.
2. Make your change, following the conventions above.
3. Run the full check suite locally (lint, format, mypy, `make test`).
4. Open a PR against `main` with a clear description of the *why*, not just the *what*.
5. Be responsive to review feedback — small, iterative PRs move faster than large ones.

By contributing, you agree your contribution is licensed under this project's
[Apache License 2.0](LICENSE), per the License's own terms on Contributions (§5).
