# Proposal — Module 8: Live adapters (IBM Runtime + Braket) with cost guardrails

*Design proposal only; no implementation in this change. This file lives at the
repo root for review visibility and is deleted before the module merges (flagged
choice: a `design/` folder would preserve history, but prior modules used a
root-level `PROPOSAL-module-N.md` that is removed on merge — keeping that
convention).*

**Goal:** a sealed QPR from a real quantum computer, with guardrails that make
accidental spending impossible.

**Non-negotiable design principles** (restated from the module spec; everything
below serves them):

1. The default is "no": live execution requires the `--live` CLI flag AND
   credentials present AND the cost gate passing. Any missing layer → typed
   refusal naming exactly what is missing. No environment variable can enable
   live mode alone.
2. Cost is checked before submit, always. `estimate_cost()` runs before every
   live submission; estimate over cap OR undeterminable → refuse. Default
   monetary cap 0.00.
3. Two budgets: monetary and QPU-runtime seconds, capped per calendar month,
   configured only in a config file — never in CLI flags.
4. Cumulative accounting: append-only local spend ledger; advisory client-side
   bookkeeping (provider-side billing alarms remain the real backstop).

A reference spike of this module exists (parked, pre-rename). It is consulted
throughout and §8 lists everything this proposal deliberately does differently.

---

## 1. Component breakdown

New subpackage `veriqant_bench.live/` (provider-independent guardrail and
lifecycle machinery), two new adapter modules, and small extensions to existing
files:

```
src/veriqant_bench/
  live/
    __init__.py     public exports: SpendLimits, load_limits, SpendLedger,
                    check_cost_gate, LiveAdapterBase
    limits.py       SpendLimits model + limits.toml loading & precedence
    ledger.py       SpendLedger: append-only JSONL + file lock + monthly totals
    gate.py         check_cost_gate(): the single pre-submit decision procedure
    base.py         LiveAdapterBase: opt-in layers, backoff polling, transient
                    retry, JobHandle persistence, resume attachment
  adapters/
    ibm.py          IBMRuntimeAdapter ([ibm] extra)
    braket_aws.py   BraketAdapter ([braket] extra; reuses braket_local's
                    QASM3→Braket conversion)
    errors.py       + LiveRefusedError, CostGateError, CredentialError
    types.py        + CostEstimate.qpu_seconds
    registry.py     + install hints for ibm_runtime / braket_aws
  benchmarks/
    base.py         default execute() records benchmark context in
                    JobSpec.metadata (enables resume)
    runner.py       + resume_run(); ledger/cost/timing folded into the QPR
  cli.py            + --live / --device on run commands; `jobs resume`;
                    `limits show`; probe behavior for live adapters
scripts/first_light.py
docs/LIVE.md
tests/
  live/             test_limits.py, test_ledger.py, test_gate.py,
                    test_concurrency.py, test_resume.py
  adapters/         test_ibm_runtime.py, test_braket_aws.py,
                    test_live_conformance.py (fakes in CI; real devices
                    behind --live-conformance)
```

**How it attaches to existing machinery:**

- *Adapter registry:* `ibm_runtime` and `braket_aws` register in the existing
  `veriqant_bench.adapters` entry-point group. Missing extras keep showing as
  "unavailable + install hint", never import errors. The `[ibm]` extra gains
  `qiskit-qasm3-import` (the live path parses OpenQASM 3 via `qiskit.qasm3`,
  which requires it).
- *Lifecycle:* `LiveAdapterBase` is the live sibling of `LocalAdapterBase`:
  same `QPUAdapter` protocol, same typed error hierarchy, same
  `AwaitResultMixin` shape — but `await_result` uses exponential backoff with
  jitter instead of a fixed interval, and jobs survive process death via
  persisted handle files. Provider status strings are mapped onto the existing
  `JobStatus` state machine.
- *Conformance suite:* the Module 2 `AdapterConformanceSuite` runs unchanged
  against both live adapters wired to fake transports in CI, and (manually,
  behind `pytest --live-conformance`) against real devices. The suite's
  existing skips do the right thing: seed-determinism is only contractual for
  simulators, and the cost-zero check applies only when `is_simulator` is
  true.
- *Runner:* `run_benchmark()` is untouched in interface. It gains a private
  `_assemble_record()` split so `resume_run()` (new) can produce the identical
  sealed QPR an uninterrupted run would have.

**Design rule (new, explicit):** `estimate_cost()` must never touch the
network. The gate has to work offline and fail closed; both adapters estimate
from local data only (constants, a static price table, already-fetched device
identity).

## 2. Public API surface

### New classes / functions

```python
# veriqant_bench.live
class SpendLimits(BaseModel):           # frozen, extra="forbid"
    monthly_monetary_cap: Decimal = 0.00
    currency: str = "USD"               # ^[A-Z]{3}$
    monthly_qpu_seconds_cap: float = 0.0
    allow_unknown_cost: bool = False    # config-file only, documented dangerous
    source: str = "defaults"            # provenance for refusal messages

def load_limits(cwd: Path | None = None) -> SpendLimits
class SpendLedger:                       # append-only JSONL, file-locked
    def record_estimate(...) -> str      # returns entry id
    def record_actuals(entry_id, ...)    # amendment, never rewrites
    def record_released(entry_id, ...)   # provider rejected the submit
    def monthly_totals(now=None) -> MonthlyTotals
def check_cost_gate(estimate, *, limits, ledger, adapter, device,
                    now=None) -> str    # ledger entry id on pass

class LiveAdapterBase(AwaitResultMixin, ABC):
    def __init__(self, *, allow_live: bool = False, limits=None, ledger=None,
                 jobs_dir=None, ...)

# veriqant_bench.adapters
class IBMRuntimeAdapter(LiveAdapterBase):    # name="ibm_runtime"
    def __init__(self, backend_name: str | None = None, *,
                 allow_live: bool = False, ...)   # None → least busy, lazily
class BraketAdapter(LiveAdapterBase):        # name="braket_aws"
    def __init__(self, device_arn: str, *, allow_live: bool = False,
                 s3_folder: tuple[str, str] | None = None, ...)

# veriqant_bench.adapters.errors
class LiveRefusedError(SubmissionError): ...   # names the missing layer(s)
class CostGateError(LiveRefusedError): ...     # gate refusal, full context
class CredentialError(LiveRefusedError): ...   # absent/expired credentials

# veriqant_bench.adapters.types
class CostEstimate(BaseModel):
    ...                                   # existing fields unchanged
    qpu_seconds: float | None = None      # runtime-quota budget dimension
                                          # free() returns qpu_seconds=0.0

# veriqant_bench.benchmarks
async def resume_run(handle_file: Path, adapter: QPUAdapter, *,
                     timeout: float = 14_400.0) -> QuantumPerformanceRecord
class ResumeError(RuntimeError): ...
```

`CostEstimate.qpu_seconds` is an additive optional field — Module 2's contract
("simulators return `CostEstimate.free()`") is preserved.

### CLI

- `veriqant-bench run <benchmark> --adapter ibm_runtime [--device <backend>] --live`
  and `--adapter braket_aws --device <arn> --live`. `--live` is the only way
  to set `allow_live=True` from the CLI; passing it with a local adapter is an
  error ("--live has no meaning for a local adapter"). `--device` selects an
  IBM backend name (omitted → least-busy open-plan device) or a Braket device
  ARN (required for braket_aws).
- `veriqant-bench jobs resume <handle-file> [--out results/] [--timeout S]` —
  re-attach to an interrupted live job and finish it into a sealed QPR.
- `veriqant-bench limits show` — print the effective limits (and their source
  file), the ledger path, and month-to-date totals. Read-only; exists so users
  can audit the gate before and after runs.
- `veriqant-bench adapters probe <live-adapter>` prints capabilities and
  calibration but **skips the smoke-circuit run** for live adapters unless
  `--live` is passed (a probe must never be the thing that spends quota).

### Config file: `limits.toml`

```toml
# ~/.config/veriqant/limits.toml   (user-wide)
# ./veriqant-limits.toml           (repo-local; takes precedence when present)
[budgets]
monthly_monetary_cap = 0.00      # in `currency`; e.g. 5.00
currency = "USD"                  # ISO 4217; estimates in another currency are refused
monthly_qpu_seconds_cap = 0.0    # e.g. 300.0 = half the IBM open-plan ~10 min/month
allow_unknown_cost = false       # DANGEROUS: permits submits whose cost cannot be
                                 # charged to any budget before it is incurred
```

Precedence: repo-local file > user file > all-zero defaults. Unknown keys are
rejected (`extra="forbid"`), so a typo'd cap name cannot silently leave a
default in place. Monetary values are parsed through `Decimal(str(...))` —
no binary-float money.

### Ledger record format (JSONL, one object per line, append-only)

```jsonc
{"kind":"estimate","id":"<uuid>","timestamp":"<RFC3339 UTC>","adapter":"ibm_runtime",
 "device":"ibm_torino","amount":"0.00","currency":"USD","qpu_seconds":7.5,
 "heuristic":"per_circuit_1s_per_shot_1ms"}
{"kind":"actuals","ref":"<estimate id>","timestamp":"...","amount":null,"qpu_seconds":4.1}
{"kind":"released","ref":"<estimate id>","timestamp":"...","reason":"provider rejected submit"}
```

Money as strings (Decimal), never floats. The file is never rewritten;
amendments reference the original entry. Default path
`~/.config/veriqant/ledger.jsonl` (see fork F1).

## 3. QPR / schema impact

Already present in QPR 0.2.0 (no change needed): `execution.live` (the runner
sets it from `capabilities().is_simulator`), `execution.job_ids`,
`provider.name`/`provider.adapter`, full `device` identity with verbatim
`calibration_snapshot` + `calibration_snapshot_at`.

**Proposed: QPR 0.2.0 → 0.3.0** (minor, additive, two new optional objects on
`execution` — following the D7 precedent that auditable facts get structural
fields, not buried blobs):

```jsonc
"execution": {
  ...,
  "timing": {                       // optional
    "queue_seconds": 5821.4,        // ≥ 0
    "execution_seconds": 12.7,      // ≥ 0
    "source": "provider_job_metrics" // or "local_state_transitions", ...
  },
  "cost": {                         // optional; present iff live
    "ledger_entry_id": "<uuid>",          // cross-reference, local bookkeeping
    "estimated_amount": "0.00",           // decimal-as-string, never float
    "currency": "USD",
    "estimated_qpu_seconds": 7.5,
    "actual_qpu_seconds": 4.1             // optional; provider-reported
  }
}
```

Why a bump and not metadata: `Execution` has no free-form metadata slot, so
the only no-bump options are smuggling (the reference spike appended
`"veriqant-ledger:<id>"` into `job_ids`) or burying timing in
`transpilation.settings` — both are exactly what D7 rejected. Queue-vs-execution
time is a first-order auditable fact for live hardware (a 2-hour queue and a
12-second execution must be distinguishable to any consumer without parsing
conventions), and the ledger id is the spend-accountability cross-reference.
Both are optional, so every existing 0.2.0 producer path stays valid; the
verifier and TS validator accept them via normal codegen. See fork F4 for the
alternatives considered.

Release coordination: schema bump → regenerate Pydantic/TS artifacts (codegen
drift job keeps them honest) → `@veriqant/schema` 0.2.0 on npm via the
`schema-v*` tag, README compatibility table updated. Local adapters' existing
`timing` metadata (`local_state_transitions`) is promoted into
`execution.timing` by the runner, so simulator records benefit from the same
structural field.

## 4. Cost gate & ledger design

### The exact decision procedure before a submit

`LiveAdapterBase.submit(spec)` is the single chokepoint — there is no other
path to a provider `create`/`run` call:

```
0. Layer check (LiveRefusedError listing ALL missing layers at once):
   a. allow_live is True (set only by --live or explicit code)
   b. credentials present (provider hook; never written by us)
1. estimate = self.estimate_cost(spec)           # local-only, no network
2. acquire exclusive lock on the ledger file     # see concurrency below
3. gate rules, in order (CostGateError; message includes limits source,
   ledger path, month-to-date totals, and the failing arithmetic):
   a. confidence == "unknown" OR qpu_seconds is None
        → refuse unless limits.allow_unknown_cost
   b. amount == 0 AND qpu_seconds == 0 on a live adapter
        → treated as unknown (a live job that claims to cost literally
          nothing is an estimator bug, not a free job) → rule (a)
   c. estimate.currency != limits.currency AND amount > 0
        → refuse (we never guess an exchange rate)
   d. month_to_date.monetary + amount > monthly_monetary_cap → refuse
   e. month_to_date.qpu_seconds + qpu_seconds > monthly_qpu_seconds_cap → refuse
4. append the estimate entry (returns ledger_entry_id); release lock
5. provider submit — NEVER retried:
   - provider rejected synchronously (auth/validation error, never reached
     the queue) → append "released" amendment, re-raise typed
   - ambiguous failure (network timeout mid-submit: the job MAY exist)
     → keep the charge (conservative), raise SubmissionError telling the
       user to check the provider console
6. persist the handle file (JobHandle + full JobSpec + submit metadata +
   ledger_entry_id + adapter rebuild kwargs)
7. on result retrieval: append "actuals" amendment with provider-reported
   usage, fold cost+timing into the QPR
```

Default caps are 0.00 / 0.0, so out of the box rule (d) or (e) refuses
everything — including monetarily-free IBM open-plan jobs, whose
`qpu_seconds > 0` estimate exceeds the 0.0 quota cap. The user must write a
limits file before the first live shot. Rule (b) closes the remaining gap
(both-zero estimates) so "cap 0 blocks everything" is an invariant, not an
accident of the estimators.

### Failure modes

| Failure | Behavior |
|---|---|
| No `--live` | `LiveRefusedError` naming the flag |
| No credentials | `CredentialError` naming the provider's standard mechanism |
| Cost unknown | `CostGateError`; names the `allow_unknown_cost` escape hatch and its danger |
| Over either cap | `CostGateError` with the arithmetic and the limits-file path |
| Currency mismatch | `CostGateError` (no silent FX) |
| Malformed limits file | hard error naming file and key — a broken safety config never degrades to defaults silently |
| Corrupt ledger line | hard error; the gate refuses to compute totals from a file it cannot fully read (fail closed) |
| Provider rejects submit | typed error + `released` amendment (budget returned) |
| Ambiguous submit failure | budget stays committed; user pointed at provider console |

### Concurrency (two CLI invocations at once)

The gate is check-then-append; without mutual exclusion two processes can both
read month-to-date totals that exclude each other's pending estimate and both
pass a cap with room for only one. Steps 2–4 therefore hold an exclusive
OS-level file lock on the ledger (`fcntl.flock` on POSIX, `msvcrt.locking` on
Windows — two small stdlib branches, no new dependency). Lock acquisition uses
a short timeout (~10 s) and refuses loudly on contention rather than blocking
forever. The reference spike has no locking; this is a deliberate fix.

### Month rollover

Totals are computed per UTC calendar month, keyed by the **estimate entry's**
timestamp. Amendments (`actuals`, `released`) attach to their estimate via
`ref` and apply to the estimate's month regardless of when the amendment was
written — a job submitted June 30 and finishing July 1 amends June. (The
reference spike filtered amendments by their own timestamp, silently dropping
cross-month corrections.) Per entry, the gate charges
`max(estimate, actual)` — actuals can raise the committed figure, never lower
it below the estimate. `released` entries zero their estimate's contribution.
A new month starts at zero by construction; no reset step exists or is needed.

### IBM quota-seconds estimation

The open plan is monetarily free but quota-limited (~10 minutes/month), so the
QPU-seconds estimate is the binding number. Options in fork F3; the
recommendation (documented constants, conservative, amended by actuals) is
summarized there. Whatever heuristic produced an estimate is named in the
ledger entry (`"heuristic": ...`) so a later audit can tell which model was in
force.

## 5. Resilience design

### JobHandle serialization

Every accepted submission writes
`~/.config/veriqant/jobs/<adapter>_<sanitized-job-id>_<digest12>.json`:

```jsonc
{
  "adapter": "ibm_runtime",
  "adapter_kwargs": {"backend_name": "ibm_torino"},   // rebuild at resume
  "handle": { ... JobHandle ... },
  "spec": { ... full JobSpec incl. benchmark context in metadata ... },
  "submit_metadata": { ... transpilation record, task ARNs ... },
  "ledger_entry_id": "<uuid>",
  "calibration_at_submit": { ... CalibrationSnapshot ... }   // see below
}
```

The filename digest handles provider ids that are ARNs (slashes, colons).
The benchmark's default `execute()` puts `{benchmark, params, seed, shots}`
into `JobSpec.metadata`, which is what makes the run reconstructible —
generation is deterministic from (params, seed).

### Resume semantics (`veriqant-bench jobs resume <file>`)

1. Read the handle file; rebuild the adapter from `adapter` +
   `adapter_kwargs` (still requires `--live` semantics? **No** — resuming
   polls and fetches results, it never submits, so resume requires
   credentials but not the live flag or the cost gate; nothing new can be
   spent).
2. Regenerate the circuits from the recorded benchmark context and compare
   them source-for-source against the submitted `spec.circuits`. Any mismatch
   (SDK or benchmark version drift since submission) → `ResumeError`; we
   refuse to assemble a record that misdescribes the executed job.
3. Re-attach to the provider job by id (`_attach` hook), `await_result` with
   backoff, then assemble and seal exactly as `run_benchmark` would —
   including the persisted `ledger_entry_id` into `execution.cost` (the spike
   lost this cross-reference on resume; fixed by reading it from the handle
   file, not from adapter instance state).
4. Benchmarks that override `execute()` with multi-submission protocols
   (throughput's timed batches) are **not resumable** and say so in a typed
   error. Their timing semantics don't survive interruption anyway.

Calibration: the snapshot captured at submit time is persisted in the handle
file and used for the resumed QPR's `device.calibration_snapshot`, with
`calibration_snapshot_at` telling the truth about when it was retrieved. A
resume-time re-fetch would describe a machine state the job may not have run
under; submit-time is the defensible default. (Open question Q5: for
multi-hour queues even submit-time calibration predates execution — recorded
honestly via the timestamp, and noted in `docs/LIVE.md`.)

### Credential expiry mid-poll

Provider auth exceptions during `poll`/`result` are caught at the
`LiveAdapterBase` boundary and re-raised as `CredentialError` (the typed
hierarchy promise: backend exceptions never escape bare). The handle file is
untouched by the failure, so the recovery path is: re-authenticate, then
`jobs resume <file>`. Transient network errors (`ConnectionError`, timeouts,
`OSError`) retry idempotent reads up to 3 times with linear backoff; auth
errors do not retry (retrying an expired token is noise). Submits are never
blind-retried under any classification.

### Polling

Exponential backoff with jitter: 2 s initial, ×1.6, capped at 60 s, ±20%
jitter. Default `await_result` timeout for live adapters: 4 hours; on timeout
the error message names the handle file and the exact resume command. The
protocol's `poll_interval` parameter is accepted for compatibility and
ignored in favor of backoff (documented).

## 6. Test strategy

CI has no QPU, no credentials, and must stay that way (green CI must never
depend on a queue). Three rings:

**Ring 1 — pure unit tests (no provider SDKs):** `live/` is provider-free by
construction. Limits parsing + precedence (repo-local over user over defaults,
malformed file refusal, unknown-key refusal); ledger append/amend/released,
month attribution, corrupt-line refusal; gate decision table — every rule in
§4 gets a test, including: cap-0 blocks a paid estimate, cap-0 blocks a
free-but-quota estimate, unknown confidence blocks, missing qpu_seconds
blocks, both-zero-estimate blocks, currency mismatch blocks,
`allow_unknown_cost` admits, accumulation across multiple entries, simulated
month rollover and cross-month amendment (every gate/ledger API takes
`now: datetime | None` precisely so tests inject time — no clock patching),
and a concurrency test: N threads/processes race the gate against a cap with
room for one — exactly one passes.

**Ring 2 — full adapter paths against fakes (CI):**

- IBM: `qiskit-ibm-runtime` `FakeBackend` classes (e.g. `FakeManilaV2`)
  supply real coupling maps, properties, and ISA targets; a small
  `FakeRuntimeService` + fake Sampler job (deterministic counts, scripted
  status sequences, scripted `metrics()` payloads) covers submit → poll →
  result, calibration mapping (T1/T2/gate/readout errors land verbatim in the
  snapshot), queue/exec timestamp extraction, usage→actuals amendment,
  open-plan refusal of a premium-looking account, and result-register
  variants (`data.c` vs `data.meas` vs named registers — the SamplerV2 result
  shape has drifted across releases and the fake matrix pins each variant).
- Braket: stubbed `AwsDevice`/`AwsQuantumTask` (constructor-injected
  `device_factory`; boto3 session stubbed for the credentials check) covers
  conversion reuse incl. `UnsupportedCircuitError`, price-table hit/miss →
  estimate/unknown, availability-window warning surfaced and recorded,
  batch status aggregation, and bit-order reversal into QPR convention.
- The Module 2 conformance suite runs against both fake-backed adapters in CI
  (permissive limits + tmp ledger fixtures, so the gate exercises its pass
  path too).
- End-to-end: `run rb --adapter ibm_runtime --live` against fakes produces a
  sealed, self-verified QPR with `execution.live=true`, `execution.cost`,
  `execution.timing`, and the ledger entry present; resume tests kill the
  wait, rebuild from the handle file, and verify the resumed QPR seals
  identically in structure (and that drifted regeneration refuses).

**Ring 3 — `pytest --live-conformance` (manual, never CI):** the same
conformance suite instantiated against real devices, gated by a registered
`live_conformance` marker that auto-skips without the flag. Requires
credentials and a limits file; the IBM run fits in ~1 minute of quota; the
Braket run additionally requires an explicit device ARN via environment
selection (selection only — the env var cannot enable live mode; `allow_live`
is still set explicitly in the test). These tests override the 60 s
pytest-timeout with a per-test marker sized for real queues, and carry the
`slow` marker.

Coverage: the ≥90% full-suite gate stays. Fakes cover all decision logic; the
only `pragma: no cover` lines are the thin real-network constructors/attach
calls, kept to single statements. mypy `--strict` throughout; new overrides
for `qiskit_ibm_runtime.*` and `boto3.*`.

## 7. Design forks

### F1 — Ledger location & format

| Option | Trade-offs |
|---|---|
| **(a) JSONL at `~/.config/veriqant/ledger.jsonl` (XDG; next to `limits.toml`)** — recommended | Human-greppable, append-only matches the audit posture, one budget per human (caps are about a person's money/quota, not a repo), trivially inspectable in bug reports. Not transactional — needs the file lock from §4. |
| (b) SQLite ledger | Real transactions, free concurrency. But an opaque binary file in a product whose ethos is inspectable records; schema migrations for a bookkeeping file; harder "cat the ledger" support story. |
| (c) Repo-local ledger next to the repo-local limits file | Per-project budgets compose badly with per-person caps — two repos each with a 300 s cap silently doubles real spend against one provider account. |

Recommendation: **(a)**. One user-level ledger, locked, JSONL. A repo-local
*limits* override stays (caps can be tightened per project), but spend always
accumulates in the one user ledger so the monthly total is a true total per
account-holder.

### F2 — Braket price-table maintenance

| Option | Trade-offs |
|---|---|
| **(a) Static table in code: per-task + per-shot by device family, with `PRICE_TABLE_VERIFIED_DATE` + source URL recorded in capabilities and the QPR** — recommended | Offline, deterministic, auditable, fail-closed (unknown device → unknown cost → refused). Goes stale; needs a human re-verification ritual. |
| (b) AWS Price List API at estimate time | Always current in principle. But puts a network call inside the safety gate (gate must work offline and fail closed), needs extra IAM permissions, and the API's SKU structure for Braket is not stable enough to trust unattended. |
| (c) Committed data file refreshed by a scheduled CI job | Automates staleness away, but a bot updating the numbers that gate real spending inverts the trust model — price changes should pass a human eye. |

Recommendation: **(a)** plus an explicit staleness rule: when the table's
verification date is older than 90 days, the gate still passes but emits a
loud warning naming the date and source URL, and the estimate stays
confidence `"estimate"` (never `"exact"` — prices are a model, not a quote).
Re-verifying the table becomes a release-checklist item.

### F3 — IBM quota-seconds heuristic

| Option | Trade-offs |
|---|---|
| **(a) Documented conservative constants: `circuits × 1.0 s + circuits × shots × 1 ms`** — recommended | Honest about being a model; deliberately over-estimates (typical repetition delay ~250 µs + readout, rounded up generously), so the gate errs toward refusal. Cheap, offline, explainable in one line. Can over-refuse tight budgets. |
| (b) Transpile first, sum scheduled circuit durations × shots + per-circuit overhead | More accurate per device. But requires transpilation before the gate (work before refusal), depends on backend timing data being present, and projects false precision — queue-side overheads dominate and aren't modeled anyway. |
| (c) Flat per-submission block (e.g. charge 60 s per job) | Simplest possible; brutally conservative; makes small-shot experiments cost the same as large ones, which misrepresents the thing the ledger claims to track. |

Recommendation: **(a)**, with the heuristic name recorded in the ledger entry
and provider-reported usage (`job.metrics()["usage"]`) amending the ledger
after every job. Honesty note for `docs/LIVE.md`: the estimate is a
deliberate over-bound; the ledger converges toward truth via actuals; the
provider dashboard remains the authoritative quota meter. (b) can be a later
upgrade behind the same gate interface if first-light data shows (a) is too
crude.

### F4 — Where queue/execution timing and the ledger reference live

| Option | Trade-offs |
|---|---|
| **(a) Schema 0.3.0: optional structural `execution.timing` + `execution.cost`** — recommended | Visible to every consumer without convention-parsing; validated shapes; D7 precedent (structural visibility beat buried blobs for `Metric.quality`). Cost: codegen + npm release + compat-table update. |
| (b) Execution metadata only (today that means `transpilation.settings` or `results.analysis`) | No release work. But there is no legitimate free-form slot on `execution` — both homes misdescribe the data, and consumers must know magic keys. |
| (c) The spike's approach: `"veriqant-ledger:<id>"` prefixed into `job_ids` | Zero schema work, maximally buried; a fake "job id" in a field documented as provider job identifiers is the kind of convention D7 exists to prevent. |

Recommendation: **(a)**. One minor bump carries both fields; everything is
optional so all existing producers/records remain valid.

### F5 — When the ledger charge is committed

| Option | Trade-offs |
|---|---|
| **(a) Charge at gate-pass (pre-submit); append `released` if the provider synchronously rejects; keep the charge on ambiguous failure** — recommended | Can never under-count: the budget is committed before any provider interaction. Failed submits don't permanently burn budget (released), but a crash in the submit window leaves a conservative phantom charge until investigated. |
| (b) Charge only after successful submit | Never charges for non-jobs, but a crash between provider-accept and ledger-append under-counts real spending — the one failure mode a spend guardrail must not have. |

Recommendation: **(a)**. Doubt resolves against the budget, mirroring D13's
"doubt resolves against the claim".

## 8. Risks, open questions, and deliberate departures from the reference spike

### Departures (each is a conscious decision, not drift)

1. **Ledger file locking added** — the spike's gate has a check-then-append
   race; two concurrent invocations can both pass a one-job budget (§4).
2. **Ledger reference into the QPR structurally**, not as a fake job id
   (fork F4c rejected).
3. **Resume recovers `ledger_entry_id` from the handle file**, not from
   `adapter.last_ledger_entry_id` instance state — in the spike a resumed
   record silently lost its spend cross-reference, and instance state ties
   one adapter object to one in-flight job.
4. **Lazy IBM backend resolution.** The spike resolves the backend (a network
   call requiring credentials) in `__init__`, so merely constructing the
   adapter — `adapters list`/`probe`, tests — touches the network and can
   raise before any layer check runs. Resolution moves to first use;
   construction is free and offline.
5. **Cross-month amendment fix** — the spike attributes amendments to the
   amendment's month, dropping late actuals; here amendments follow their
   estimate's month via `ref` (§4).
6. **Both-zero estimates are refused** (gate rule b) — the spike would pass
   an `amount=0, qpu_seconds=0` estimate through a cap-0 gate.
7. **`CredentialError` for mid-poll auth failures** — the spike lets provider
   auth exceptions escape as generic retries/`ExecutionError`; expiry is a
   distinct, recoverable condition with a documented resume path (§5).
8. **Provider-reported timestamps for `started_at`/`completed_at`** where the
   API offers them — the spike stamps both with `datetime.now()` at
   retrieval, which misdescribes a job that executed hours earlier. Fallback
   to retrieval time only when the provider exposes nothing, with
   `timing.source` saying so.
9. **Calibration for resumed runs from the submit-time snapshot persisted in
   the handle file** — the spike re-fetches at resume time and annotates;
   recording a calibration the job demonstrably didn't run under (machine
   recalibrated during the queue) is the wrong default for an auditor (§5).
10. **`adapters probe` made safe for live adapters** (no implicit smoke
    submission; §2).
11. **Open-plan detection hardened** — the spike substring-matches `"open"`
    across account fields, which is both spoofable and brittle. v1 checks the
    structured plan/instance fields qiskit-ibm-runtime actually exposes and
    refuses when the plan is *undeterminable* (fail closed), not only when it
    is recognizably premium. Exact field names to be pinned against a real
    account in Session 2 (Q1).

### Risks & open questions

- **Q1 — IBM plan detection:** which structured account/instance fields
  reliably identify the open plan across qiskit-ibm-runtime versions? Needs
  one manual check against a real account before implementation hardens; the
  fallback posture is refuse-when-unsure.
- **Q2 — SamplerV2 result shape drift:** register naming and `BitArray`
  access have changed across qiskit-ibm-runtime releases. Mitigation: the
  fake matrix pins each known variant; the `[ibm]` extra gets a tested
  version floor, and an unrecognized result shape is a typed error, not a
  guess.
- **Q3 — Braket bit order on real hardware:** the reversal from Braket's
  measurement-count key order into QPR convention is asserted by the
  conformance suite's asymmetric-circuit test on fakes; it must be confirmed
  once on a real device via `--live-conformance` before any published record.
  This is exactly what the suite's bit-order test exists for.
- **Q4 — IBM `max_shots`:** not uniformly exposed on `BackendV2`. Capability
  mapping treats it as optional and the fake matrix covers both presence and
  absence.
- **Q5 — Calibration vs. multi-hour queues:** even submit-time calibration
  predates execution for long-queued jobs. Recorded honestly
  (`calibration_snapshot_at`), documented in `docs/LIVE.md`; a
  post-completion re-fetch as a *second* snapshot would need a schema slot —
  deliberately out of scope for v1.
- **Q6 — Windows file locking:** `msvcrt.locking` semantics differ from
  `flock` (mandatory, byte-range). The lock helper needs its own small test
  matrix; CI runs Linux only, so the Windows branch is best-effort and
  documented as such.
- **Q7 — Ledger as shared state for parallel benchmarks:** Module 8 keeps
  one submission in flight per CLI invocation; nothing prevents a user
  scripting parallel invocations, which the lock serializes at the gate. Fine
  at this scale; revisit if a future module batches aggressively.
- **Q8 — `first_light.py` parameters:** 1Q RB, `lengths=[1,2,4]`,
  2 samples/length, 256 shots → 6 circuits, estimated ~7.5 quota-seconds
  under heuristic F3(a) — comfortably under the one-minute target. The script
  is documented, manual, and run personally by the maintainer; CI and
  automation never execute it. It prints the estimate and the target device
  before the gate, and the sealed QPR path + content hash after.
- **Q9 — Schema release sequencing:** the 0.3.0 schema bump should land and
  publish before or with the adapter code that emits the new fields, to keep
  the npm validator ahead of produced records. Sequenced as: schema PR →
  `schema-v0.2.0` tag → adapters PR.

## Out of scope (unchanged from the module spec)

Azure Quantum; premium IBM plans; Runtime sessions; QEC on live hardware;
any service-side ingestion; accounts/billing.

## Documentation deliverables

- `docs/LIVE.md` — credentials per provider (never written by us), the two
  budgets and `limits.toml`, the ledger and its advisory nature (with
  pointers to AWS Budgets and the IBM usage dashboard as the real backstops),
  queue/backoff/resume walkthrough, first-light walkthrough, and the
  `--live-conformance` ritual.
- `README.md` — status section gains the live-execution milestone with the
  same honesty framing (live records are new; longitudinal claims need
  longitude).
- `docs/ARCHITECTURE.md` — adapter section updated when the module lands.
