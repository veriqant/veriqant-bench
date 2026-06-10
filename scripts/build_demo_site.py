"""Build the public demo site: landing page + pre-generated sample reports.

Everything on the page is produced by the released tooling itself — real
benchmark runs (on simulators), real sealed QPRs, real reports — and the
page says so explicitly. These are demonstrations of the report format and
methodology, not device comparisons: no rankings, no device names beyond
"Aer (simulated)".

Usage: python scripts/build_demo_site.py <output-dir>
Requires: veriqant-bench[local,qec] installed (the pages workflow uses the
repo's own environment).
"""

from __future__ import annotations

import asyncio
import html
import sys
from datetime import UTC, datetime
from pathlib import Path

from veriqant_bench import __version__
from veriqant_bench.adapters import NoiseSpec
from veriqant_bench.adapters.aer import AerSimulatorAdapter
from veriqant_bench.benchmarks import run_benchmark
from veriqant_bench.benchmarks.qec.memory import RepetitionMemory, RepetitionParams
from veriqant_bench.benchmarks.qv import QuantumVolume, QVParams
from veriqant_bench.benchmarks.rb import RandomizedBenchmarking, RBParams
from veriqant_bench.qpr import QuantumPerformanceRecord, dump_qpr, verify_qpr_file
from veriqant_bench.report import load_verified_records, render_report

SEED = 20260610
NOISE = NoiseSpec(depolarizing_1q=0.01, depolarizing_2q=0.04)


async def build_records() -> dict[str, QuantumPerformanceRecord]:
    rb = RandomizedBenchmarking()
    rb_params = RBParams(qubits=[0], lengths=[1, 2, 4, 8, 16, 32], samples_per_length=8)
    qv = QuantumVolume()
    qec = RepetitionMemory()
    return {
        "rb-ideal": await run_benchmark(
            rb, AerSimulatorAdapter(), rb_params, seed=SEED, shots=512
        ),
        "rb-noisy": await run_benchmark(
            rb, AerSimulatorAdapter(noise=NOISE), rb_params, seed=SEED, shots=512
        ),
        "qv-demo": await run_benchmark(
            qv,
            AerSimulatorAdapter(noise=NoiseSpec(depolarizing_1q=0.002, depolarizing_2q=0.008)),
            QVParams(widths=[2, 3, 4], circuits_per_width=50),
            seed=SEED,
            shots=256,
        ),
        "qec-criteria-demo": await run_benchmark(
            qec,
            AerSimulatorAdapter(noise=NOISE),
            RepetitionParams(distances=[3, 5, 7], rounds=7, criteria="ab-lq-2026"),
            seed=SEED,
            shots=2000,
        ),
    }


REPORTS = {
    "rb": ("Randomized benchmarking — ideal vs. noisy", ["rb-ideal", "rb-noisy"]),
    "qv": ("Quantum Volume", ["qv-demo"]),
    "qec": ("QEC memory + criteria scorecard", ["qec-criteria-demo"]),
}


def build_site(out_dir: Path) -> None:
    records = asyncio.run(build_records())
    records_dir = out_dir / "records"
    reports_dir = out_dir / "reports"
    records_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    for name, record in records.items():
        path = records_dir / f"{name}.qpr.json"
        dump_qpr(record, path)
        report = verify_qpr_file(path)
        if not report.ok:  # pragma: no cover - generation bug guard
            raise RuntimeError(f"demo record failed verification: {path}: {report.issues}")

    generated_at = datetime.now(tz=UTC)
    for slug, (_, names) in REPORTS.items():
        loaded = load_verified_records([records_dir / f"{name}.qpr.json" for name in names])
        (reports_dir / f"{slug}.html").write_text(
            render_report(loaded, generated_at=generated_at), encoding="utf-8"
        )

    (out_dir / "index.html").write_text(landing_page(records, generated_at), encoding="utf-8")
    print(f"site written to {out_dir}")


def landing_page(
    records: dict[str, QuantumPerformanceRecord], generated_at: datetime
) -> str:
    report_cards = "\n".join(
        f"""<li><a href="reports/{slug}.html">{html.escape(title)}</a>
 — records: {", ".join(f'<a href="records/{name}.qpr.json"><code>{name}.qpr.json</code></a>' for name in names)}</li>"""
        for slug, (title, names) in REPORTS.items()
    )
    demo_hash = records["rb-ideal"].integrity.content_sha256
    timestamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>veriqant-bench — reproducible QPU benchmarking with sealed records</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 46rem; color: #1a1a2e; line-height: 1.55;
       padding: 0 1rem; }}
h1 {{ font-size: 1.7rem; margin-bottom: 0.2rem; }}
h2 {{ font-size: 1.15rem; margin-top: 2.2rem; }}
.tagline {{ color: #444; font-size: 1.05rem; }}
.notice {{ background: #fdf3d7; color: #7a5d0c; padding: 0.7rem 1rem;
          border-radius: 0.5rem; font-size: 0.9rem; }}
pre {{ background: #f4f4f8; padding: 0.8rem 1rem; border-radius: 0.5rem;
      overflow-x: auto; font-size: 0.85rem; }}
code {{ font-family: ui-monospace, Menlo, monospace; }}
.meta {{ color: #666; font-size: 0.8rem; }}
li {{ margin: 0.4rem 0; }}
</style>
</head>
<body>
<h1>veriqant-bench</h1>
<p class="tagline">Independent, reproducible QPU benchmarking. Every run
emits a sealed <strong>Quantum Performance Record</strong> that anyone can
re-verify — schema, circuit hashes, content seal, statistics included.</p>

<p class="notice"><strong>These are simulator demonstrations</strong> of the
report format and methodology. Every record below was produced on Aer
(simulated), is machine-flagged as such, and ranks nothing. Hardware results
come when the live adapters land — not before.</p>

<h2>Sample reports</h2>
<ul>
{report_cards}
</ul>

<h2>The QPR format</h2>
<p>A sealed record guarantees: the exact circuits (OpenQASM&nbsp;3 with
SHA-256 hashes), the master seed, the full transpiler configuration, raw
measurement counts, SDK versions, and a content seal over all of it. Every
metric carries sample size and a confidence interval; estimates that fail
their own quality diagnostics are published as flagged-unreliable.
<a href="https://github.com/veriqant/veriqant-bench/blob/main/docs/QPR-SPEC.md">
Read the specification</a> (CC&nbsp;BY&nbsp;4.0).</p>

<h2>Verify one yourself — about a minute</h2>
<p>Don't take this page's word for anything. Download a record and check it
with the same open-source verifier:</p>
<pre><code>pip install veriqant-bench
curl -O {html.escape("records/rb-ideal.qpr.json")}   # or click a record link above
veriqant-bench verify rb-ideal.qpr.json</code></pre>
<p>The verifier re-derives every circuit hash and the content seal locally.
Expected output ends with <code>OK: ... valid, internally consistent
QPR</code>; this record's seal is
<code>{html.escape(demo_hash[:16])}…</code>. Change a single byte in the
file and verification fails.</p>

<h2>Status</h2>
<p>Simulator-validated today (closed-loop against analytic noise injection
and the Stim oracle); live hardware adapters are the next milestone.
Veriqant is not affiliated with any quantum hardware vendor.</p>

<p class="meta">generated {html.escape(timestamp)} · veriqant-bench
{html.escape(__version__)} ·
<a href="https://github.com/veriqant/veriqant-bench">source</a> ·
Apache-2.0</p>
</body>
</html>
"""


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: build_demo_site.py <output-dir>")
    build_site(Path(sys.argv[1]).resolve())
