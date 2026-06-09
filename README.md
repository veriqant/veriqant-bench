# Veriqore

Independent observability & verification platform for quantum compute.
Hardware-agnostic benchmarking, monitoring, and verification of QPU
performance across cloud providers — a neutral layer between QPU vendors and
the people who buy their compute.

- **`packages/bench`** — `veriqore-bench`, the open-source Python SDK:
  standardized, reproducible benchmark suites that emit signed, verifiable
  **Quantum Performance Records (QPRs)**.
- **`packages/schema`** — the QPR JSON Schema (single source of truth) with
  generated TypeScript types and an Ajv validator.
- **`apps/api`** — telemetry ingestion + query API (Fastify/tRPC, in progress).
- **`apps/web`** — comparison dashboard (Next.js, in progress).

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/QPR-SPEC.md](docs/QPR-SPEC.md).

## Quick start

```bash
# TypeScript workspace
pnpm install
pnpm -r build && pnpm -r test

# Python SDK
cd packages/bench
uv sync
uv run pytest
uv run veriqore-bench verify ../schema/examples/qpr-rb-example.json
```

Everything runs offline against local simulators; live QPU execution is
opt-in (`--live`) and cost-capped.
