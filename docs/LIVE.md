# Live execution

veriqant-bench can run benchmarks on real quantum hardware (IBM Quantum and
AWS Braket). **The default is "no"**: out of the box, no live submission is
possible. Three independent layers must all pass, and any missing layer is a
typed refusal naming exactly what's missing:

1. **The `--live` flag** (or `allow_live=True` in code). No environment
   variable can enable live mode on its own.
2. **Credentials**, present via the provider's standard mechanisms — we
   never write or store them.
3. **The cost gate**, checked before *every* submission against your limits
   file and the local spend ledger. Default caps are **0.00 money and 0.0
   QPU-seconds per month** — even monetarily-free jobs are blocked until you
   write a limits file.

## The two budgets

Money is not the only thing a job spends. IBM's open plan is monetarily free
but consumes a roughly-10-minutes-per-month runtime quota; Braket bills per
task + per shot. Both budgets are capped independently, per calendar month
(UTC):

```toml
# ~/.config/veriqant/limits.toml  (user-level: the only file that grants budget)
[budgets]
monthly_monetary_cap = 0.00       # e.g. 5.00 to allow ~$5 of Braket jobs
currency = "USD"
monthly_qpu_seconds_cap = 0.0     # e.g. 300.0 = half the IBM open quota
allow_unknown_cost = false        # DANGEROUS — see below
```

A repo-local `./veriqant-limits.toml` may additionally **tighten** the
user-level limits for one project — lower a cap, or keep
`allow_unknown_cost` off — but can never loosen them: caps it declares take
the elementwise minimum, `allow_unknown_cost` can only be forced off, and
the currency cannot change. Budgets belong to you, not to a repository, so
`cd`-ing into an untrusted repo that ships a generous limits file cannot
weaken your caps (and without a user-level file, a repo-local file grants
nothing at all).

Caps live only in config files, never in CLI flags: a typo'd flag must not be
able to raise a limit. A malformed limits file is a hard error, never a
silent fall-back to defaults. Jobs whose cost cannot be estimated (an unknown
Braket device, a price table nobody has re-verified in over 180 days) are
refused unless `allow_unknown_cost = true` — dangerous by definition, because
an unknown cost cannot be checked against any budget before it is incurred.

## The ledger

Every gated submission appends an estimate entry to
`~/.config/veriqant/ledger.jsonl` (append-only; never rewritten).
Provider-reported actual usage amends entries conservatively — an amendment
can raise the committed figure, never lower it — and a submission the
provider rejected outright is released back to the budget. The gate checks
month-to-date totals + the new estimate against your caps under an exclusive
file lock, so caps are cumulative and two concurrent invocations cannot both
squeeze through the same headroom. Inspect everything at any time:

```bash
veriqant-bench limits show
```

**The ledger is advisory client-side bookkeeping.** It cannot see jobs
submitted from other machines or tools. Set provider-side billing alarms as
the real backstop:
[AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html)
for Braket, and the usage panel on the
[IBM Quantum Platform](https://quantum.cloud.ibm.com/) dashboard.

## IBM Quantum (`--adapter ibm [--device <backend>] --live`)

- Credentials: `export QISKIT_IBM_TOKEN=...` or a saved
  `QiskitRuntimeService.save_account()` account.
- Open plan only in v1. The plan is read from the account's structured
  fields; an account whose plan cannot be determined is refused (fail
  closed) rather than risked. SamplerV2 in job mode — sessions are
  deliberately unsupported (a session reserves paid time).
- Quota estimate per job: `circuits × 1.0 s + circuits × shots × 1 ms`. The
  estimate deliberately over-bounds: refusing too often is safe and
  annoying; refusing too rarely is the failure mode this system exists to
  exclude. Provider-reported usage amends the ledger after every job; the
  provider dashboard remains the authoritative quota meter.
- Calibration (T1/T2, gate/readout errors, calibration timestamps) is
  recorded verbatim in every QPR, captured at submit time.
- Queue time and execution time are recorded separately
  (`execution.timing`) — a two-hour queue is provider load, not device
  performance.

## AWS Braket (`--adapter braket_aws --device <arn> --live`)

- Credentials/region from standard AWS configuration; results land in the
  account's default Braket S3 location unless configured otherwise.
- Costs come from a static price table (per-task + per-shot by device
  family); its verification date and source URL are recorded in every
  capabilities payload. Unknown device → cost unknown → refused (see above).
  A table older than 90 days warns loudly; older than 180 days it counts as
  unknown and the gate refuses.
- Devices have operating windows; submitting outside one queues until the
  next window — the adapter warns at submit and records the warning.
- **Validation status:** the conversion of Braket's measurement-count key
  order into the QPR bit convention is pinned by tests against fakes, but
  **no Braket-sourced QPR may be published until that bit order has been
  confirmed once on real hardware** via `pytest --live-conformance`. First
  light is IBM-only; live Braket validation is a separate, deliberate,
  budgeted decision.

## Long queues, interruptions, resume

Live queues run minutes to hours. Polling backs off exponentially
(2 s → ×1.6 → 60 s cap, jittered); status polls retry through transient
network errors, but a submit is never blindly retried, and an ambiguous
submit failure keeps its budget charge (the job may exist — check the
provider console). Every accepted submission writes a handle file to
`~/.config/veriqant/jobs/`. If your process dies or times out:

```bash
veriqant-bench jobs resume ~/.config/veriqant/jobs/<file>.json --out results/
```

This re-attaches to the provider job, regenerates the circuits
deterministically from the recorded seed/parameters (verified
source-for-source against what was submitted; any drift refuses), and
assembles the same sealed QPR the uninterrupted run would have produced —
including the spend-ledger cross-reference and the calibration snapshot
captured at submit time. Resuming never submits anything, so it needs
credentials but not `--live` or the cost gate. If credentials expire while
polling, the error names the recovery path: re-authenticate, then resume the
same handle file.

Note on calibration honesty: for a long-queued job even the submit-time
snapshot predates execution; `device.calibration_snapshot_at` always tells
the truth about when it was retrieved.

## First light

`scripts/first_light.py` is the documented, manual, quota-frugal first run:
minimal 1Q RB (6 circuits × 256 shots, well under a minute of QPU time under
the documented heuristic) on the least-busy IBM open-plan device, ending in
a sealed, self-verified QPR. Read the script header for prerequisites. It is
run personally by a human maintainer; CI and automation never execute it.

## Conformance against real devices

The adapter conformance suite runs against fake transports in CI; CI never
has credentials or a QPU. To certify against a real device (manual,
quota/money-consuming, within your limits file):

```bash
uv run pytest --live-conformance tests/adapters/test_live_conformance.py
# Braket additionally needs the target device:
VERIQANT_LIVE_BRAKET_ARN=arn:aws:braket:... \
    uv run pytest --live-conformance tests/adapters/test_live_conformance.py
```

The environment variable selects the device only — it cannot enable live
mode, and the cost gate still applies.
