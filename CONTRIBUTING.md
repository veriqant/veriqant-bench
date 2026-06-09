# Contributing to veriqore-bench

Thanks for considering a contribution. This project's value is its
credibility: reproducibility, statistical honesty, and documented
methodology outrank features. PRs that trade any of those away will be
asked to change course, kindly.

## Development setup

Python side (the SDK):

```bash
cd packages/bench
uv sync                      # installs all extras + dev tools
uv run pytest                # fast suite, no coverage gate
uv run pytest -m "not slow"  # skip multi-second simulator runs
uv run pytest -n auto --cov=veriqore_bench --cov-fail-under=90   # the CI gate
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

TypeScript side (the schema package):

```bash
pnpm install
pnpm -r build && pnpm -r typecheck && pnpm -r test && pnpm lint
```

Install the git hooks once: `uvx pre-commit install`.

## The schema is generated — never edit outputs by hand

`packages/schema/schema/qpr-*.schema.json` is the single source of truth.
After changing it, regenerate everything (CI fails on drift):

```bash
pnpm --filter @veriqore/schema generate
packages/schema/scripts/generate-pydantic.sh
packages/schema/scripts/generate-example.sh
```

## Writing a third-party adapter

The adapter contract has two halves:

1. **Structural**: implement the `veriqore_bench.adapters.QPUAdapter`
   protocol and register it under the `veriqore_bench.adapters`
   entry-point group in your own package.
2. **Behavioral**: subclass
   `veriqore_bench.adapters.conformance.AdapterConformanceSuite` in your
   test suite and make it pass. The suite certifies honest job lifecycle,
   QPR bit-ordering, seed determinism on simulators, typed errors, and
   schema-valid capability reporting. An adapter that does not pass the
   conformance suite is not a Veriqore adapter.

Benchmarks and criteria profiles plug in the same way
(`veriqore_bench.benchmarks`, `veriqore_bench.criteria_profiles`).

## Methodology changes

Any change to a benchmark's circuit family, sampling procedure, or
estimator must bump that benchmark's `version` and update
`docs/BENCHMARKS.md`. Changes to the QPR format follow the semver policy in
`docs/QPR-SPEC.md` (additive optional fields = minor; anything else =
major). New criteria profiles must cite a published source — this project
executes others' norms, it does not invent them.

## Tests

- ≥90% coverage on the full suite; mypy `--strict` and ruff clean.
- Property-based tests (hypothesis) for anything that serializes.
- Multi-second simulator tests carry `@pytest.mark.slow`; everything
  respects the 60s per-test timeout.
- Closed-loop validation is the bar for new benchmarks: demonstrate the
  measured quantity against an analytic expectation or an independent
  oracle (the QEC suite uses Stim) — not just "it runs".

## Commit style

Conventional commits (`feat:`, `fix:`, `docs:`, ...). All code, comments,
and docs in English.
