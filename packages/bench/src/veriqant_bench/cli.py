"""veriqant-bench command-line interface."""

from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import click
import pydantic

from . import __version__
from .adapters import (
    AdapterUnavailableError,
    JobSpec,
    LiveRefusedError,
    NoiseSpec,
    QPUAdapter,
    list_adapters,
)
from .adapters import get as get_adapter
from .benchmarks import (
    BenchmarkUnavailableError,
    ResumeError,
    resume_run,
    run_benchmark,
    write_verified_qpr,
)
from .benchmarks import get as get_benchmark
from .qpr import QPR_VERSION, verify_qpr_file
from .report import ReportInputError, write_report

SCHEMA_RESOURCE = "qpr.schema.json"

SMOKE_CIRCUIT = (
    "OPENQASM 3.0;\n"
    'include "stdgates.inc";\n'
    "qubit[1] q;\n"
    "bit[1] c;\n"
    "h q[0];\n"
    "c[0] = measure q[0];\n"
)


@click.group()
@click.version_option(version=__version__, prog_name="veriqant-bench")
def main() -> None:
    """Standardized, reproducible benchmarks for quantum processors."""


@main.command()
@click.argument("qpr_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def verify(qpr_file: Path) -> None:
    """Verify a QPR file: schema, circuit hashes, content hash, consistency.

    Exits non-zero if any error-severity issue is found.
    """
    report = verify_qpr_file(qpr_file)
    for issue in report.issues:
        click.echo(str(issue))
    if report.ok:
        click.echo(f"OK: {qpr_file} is a valid, internally consistent QPR")
    else:
        click.echo(f"FAILED: {qpr_file} has verification errors", err=True)
        sys.exit(1)


@main.command()
def schema() -> None:
    """Print the bundled QPR JSON Schema."""
    text = files("veriqant_bench.qpr").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    click.echo(text, nl=False)


@main.command()
def version() -> None:
    """Print package and QPR schema versions."""
    click.echo(f"veriqant-bench {__version__}")
    click.echo(f"qpr-schema {QPR_VERSION}")


@main.group()
def adapters() -> None:
    """Discover and probe QPU adapters."""


@adapters.command("list")
def adapters_list() -> None:
    """List registered adapters and their availability."""
    for info in list_adapters():
        if info.available:
            click.echo(f"{info.name:<16} available    {info.description}")
        else:
            click.echo(f"{info.name:<16} unavailable  ({info.install_hint})")


async def _smoke_run(adapter: QPUAdapter, shots: int) -> dict[str, int]:
    spec = JobSpec(circuits=[SMOKE_CIRCUIT], shots=shots, seed=1234)
    handle = await adapter.submit(spec)
    result = await adapter.await_result(handle)
    return result.counts[0]


ADAPTER_ALIASES = {"aer": "aer_simulator", "braket": "braket_local", "ibm": "ibm_runtime"}
LIVE_ADAPTERS = {"ibm_runtime", "braket_aws"}
LIVE_RUN_TIMEOUT_SECONDS = 14_400.0  # live queues run minutes-to-hours


def _int_list(_ctx: click.Context, _param: click.Parameter, value: str) -> list[int]:
    try:
        return [int(item) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise click.BadParameter(f"expected comma-separated integers, got {value!r}") from exc


def _live_options(command: Any) -> Any:
    """--live / --device on every run command. --live is the ONLY way the
    CLI sets allow_live: no environment variable can enable live mode."""
    command = click.option(
        "--device",
        default=None,
        help="Live device: IBM backend name (omitted -> least busy) or Braket device ARN.",
    )(command)
    return click.option(
        "--live",
        is_flag=True,
        default=False,
        help="Enable live (paid/quota) execution; still requires credentials "
        "and a passing cost gate. See docs/LIVE.md.",
    )(command)


def _build_adapter(
    name: str,
    noise_file: Path | None,
    *,
    live: bool = False,
    device: str | None = None,
    executing: bool = False,
) -> QPUAdapter:
    resolved = ADAPTER_ALIASES.get(name, name)
    kwargs: dict[str, Any] = {}
    if resolved in LIVE_ADAPTERS:
        # Layer 1 of the live opt-in: only this flag sets allow_live; the
        # adapter still demands credentials and a passing cost gate.
        kwargs["allow_live"] = live
        if executing and not live:
            # Refuse before anything touches the network: a benchmark run on
            # a live adapter without --live can never proceed.
            raise click.ClickException(
                f"refusing to run a benchmark on live adapter '{resolved}' without "
                "--live (credentials and the cost gate still apply; see docs/LIVE.md)"
            )
        if resolved == "ibm_runtime":
            kwargs["backend_name"] = device
        if resolved == "braket_aws":
            if device is None:
                raise click.ClickException("braket_aws requires --device <device-arn>")
            kwargs["device_arn"] = device
    else:
        if live:
            raise click.ClickException(
                f"--live has no meaning for the '{resolved}' adapter (it is local)"
            )
        if device is not None:
            raise click.ClickException(
                f"--device has no meaning for the '{resolved}' adapter (it is local)"
            )
    if noise_file is not None:
        if resolved != "aer_simulator":
            raise click.ClickException(
                f"--noise is only supported by the aer_simulator adapter, not '{resolved}'"
            )
        try:
            kwargs["noise"] = NoiseSpec.model_validate_json(noise_file.read_text(encoding="utf-8"))
        except pydantic.ValidationError as exc:
            raise click.ClickException(f"invalid noise spec {noise_file}: {exc}") from exc
    try:
        return get_adapter(resolved, **kwargs)
    except (AdapterUnavailableError, LiveRefusedError) as exc:
        raise click.ClickException(str(exc)) from exc


def _resolve_seed(seed: int | None) -> int:
    if seed is None:
        seed = secrets.randbelow(2**31)
        click.echo(f"seed: {seed} (generated; pass --seed {seed} to reproduce)")
    return seed


def _execute_benchmark(
    benchmark_name: str,
    params: dict[str, Any],
    adapter: QPUAdapter,
    seed: int,
    shots: int,
    out: Path,
    *,
    live: bool = False,
) -> None:
    try:
        benchmark = get_benchmark(benchmark_name)
    except BenchmarkUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        validated = benchmark.params_model.model_validate(params)
    except pydantic.ValidationError as exc:
        raise click.ClickException(f"invalid parameters: {exc}") from exc
    timeout = LIVE_RUN_TIMEOUT_SECONDS if live else 600.0
    try:
        record = asyncio.run(
            run_benchmark(benchmark, adapter, validated, seed=seed, shots=shots, timeout=timeout)
        )
    except LiveRefusedError as exc:
        raise click.ClickException(str(exc)) from exc
    path = write_verified_qpr(record, out)
    click.echo(f"{path} {record.integrity.content_sha256}")


@main.group()
def run() -> None:
    """Run benchmarks and emit sealed, self-verified QPRs."""


@run.command("rb")
@_live_options
@click.option("--adapter", "adapter_name", default="aer_simulator", show_default=True)
@click.option(
    "--qubits",
    default="0",
    callback=_int_list,
    show_default=True,
    help="Comma-separated target qubits (1 for 1Q RB, 2 for 2Q RB).",
)
@click.option(
    "--lengths",
    default="1,2,4,8,16,32",
    callback=_int_list,
    show_default=True,
    help="Comma-separated Clifford sequence lengths.",
)
@click.option("--samples", default=10, show_default=True, help="Random sequences per length.")
@click.option("--shots", default=256, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--seed", type=int, default=None, help="Master seed; generated and printed when omitted."
)
@click.option(
    "--noise",
    "noise_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="NoiseSpec JSON file (aer_simulator only).",
)
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
def run_rb(
    live: bool,
    device: str | None,
    adapter_name: str,
    qubits: list[int],
    lengths: list[int],
    samples: int,
    shots: int,
    seed: int | None,
    noise_file: Path | None,
    out: Path,
) -> None:
    """Randomized benchmarking (1Q/2Q): error per Clifford with bootstrap CIs."""
    adapter = _build_adapter(adapter_name, noise_file, live=live, device=device, executing=True)
    _execute_benchmark(
        "rb",
        {"qubits": qubits, "lengths": lengths, "samples_per_length": samples},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
        live=live,
    )


@run.command("mirror")
@_live_options
@click.option("--adapter", "adapter_name", default="aer_simulator", show_default=True)
@click.option("--qubits", default="0,1,2", callback=_int_list, show_default=True)
@click.option(
    "--depths",
    default="2,4,8,16",
    callback=_int_list,
    show_default=True,
    help="Comma-separated half-circuit depths (layers).",
)
@click.option("--samples", default=10, show_default=True, help="Random circuits per depth.")
@click.option("--shots", default=256, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--seed", type=int, default=None, help="Master seed; generated and printed when omitted."
)
@click.option(
    "--noise",
    "noise_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="NoiseSpec JSON file (aer_simulator only).",
)
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
def run_mirror(
    live: bool,
    device: str | None,
    adapter_name: str,
    qubits: list[int],
    depths: list[int],
    samples: int,
    shots: int,
    seed: int | None,
    noise_file: Path | None,
    out: Path,
) -> None:
    """Randomized mirror circuits: success probability and polarization vs. depth."""
    adapter = _build_adapter(adapter_name, noise_file, live=live, device=device, executing=True)
    _execute_benchmark(
        "mirror",
        {"qubits": qubits, "depths": depths, "samples_per_depth": samples},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
        live=live,
    )


@run.command("qv")
@_live_options
@click.option("--adapter", "adapter_name", default="aer_simulator", show_default=True)
@click.option(
    "--widths",
    default="2,3,4",
    callback=_int_list,
    show_default=True,
    help="Comma-separated circuit widths (qubits = depth = width).",
)
@click.option(
    "--circuits",
    default=50,
    show_default=True,
    help="Random circuits per width (>=100 for publication-grade claims).",
)
@click.option("--shots", default=256, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--seed", type=int, default=None, help="Master seed; generated and printed when omitted."
)
@click.option(
    "--noise",
    "noise_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="NoiseSpec JSON file (aer_simulator only).",
)
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
def run_qv(
    live: bool,
    device: str | None,
    adapter_name: str,
    widths: list[int],
    circuits: int,
    shots: int,
    seed: int | None,
    noise_file: Path | None,
    out: Path,
) -> None:
    """Quantum volume: heavy-output probability per width, 2-sigma pass rule."""
    adapter = _build_adapter(adapter_name, noise_file, live=live, device=device, executing=True)
    _execute_benchmark(
        "qv",
        {"widths": widths, "circuits_per_width": circuits},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
        live=live,
    )


@run.command("throughput")
@_live_options
@click.option("--adapter", "adapter_name", default="aer_simulator", show_default=True)
@click.option("--batches", default=5, show_default=True, help="Sequential timed batches (R).")
@click.option("--batch-size", default=10, show_default=True, help="Circuits per batch (B).")
@click.option("--width", default=2, show_default=True, help="Template circuit width.")
@click.option("--depth", default=4, show_default=True, help="Template mirror half-depth.")
@click.option("--shots", default=256, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--seed", type=int, default=None, help="Master seed; generated and printed when omitted."
)
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
def run_throughput(
    live: bool,
    device: str | None,
    adapter_name: str,
    batches: int,
    batch_size: int,
    width: int,
    depth: int,
    shots: int,
    seed: int | None,
    out: Path,
) -> None:
    """Sequential batch throughput (NOT CLOPS — see docs/BENCHMARKS.md).

    On simulators the result measures the harness + host machine and is
    flagged accordingly. Live throughput runs are not resumable (timed
    batches do not survive interruption)."""
    adapter = _build_adapter(adapter_name, None, live=live, device=device, executing=True)
    _execute_benchmark(
        "throughput",
        {"batches": batches, "batch_size": batch_size, "width": width, "depth": depth},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
        live=live,
    )


@run.command("qec")
@_live_options
@click.option("--adapter", "adapter_name", default="aer_simulator", show_default=True)
@click.option(
    "--code",
    type=click.Choice(["repetition", "surface"]),
    default="repetition",
    show_default=True,
)
@click.option(
    "--distances",
    default="3,5,7",
    callback=_int_list,
    show_default=True,
    help="Repetition-code distances (odd, >=3).",
)
@click.option(
    "--distance",
    default=3,
    show_default=True,
    help="Surface-code distance (product path supports 3).",
)
@click.option(
    "--rounds",
    default=7,
    show_default=True,
    help="Syndrome-extraction rounds; must be >= the largest distance.",
)
@click.option(
    "--criteria",
    "criteria_profile",
    default=None,
    help="Criteria profile id (e.g. ab-lq-2026); omitted -> metrics only.",
)
@click.option("--shots", default=2000, show_default=True, type=click.IntRange(min=1))
@click.option(
    "--seed", type=int, default=None, help="Master seed; generated and printed when omitted."
)
@click.option(
    "--noise",
    "noise_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="NoiseSpec JSON file (aer_simulator only).",
)
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
def run_qec(
    live: bool,
    device: str | None,
    adapter_name: str,
    code: str,
    distances: list[int],
    distance: int,
    rounds: int,
    criteria_profile: str | None,
    shots: int,
    seed: int | None,
    noise_file: Path | None,
    out: Path,
) -> None:
    """QEC memory experiments (repetition / rotated d=3 surface code) with
    MWPM decoding and an optional logical-qubit criteria scorecard."""
    if criteria_profile is not None:
        # Cheap validation first: a typo'd profile must fail here, not after
        # the shots (and, on live hardware, the budget) are already spent.
        from .benchmarks.qec.criteria.framework import ProfileUnavailableError, get_profile

        try:
            get_profile(criteria_profile)
        except ProfileUnavailableError as exc:
            raise click.ClickException(str(exc)) from exc
    adapter = _build_adapter(adapter_name, noise_file, live=live, device=device, executing=True)
    if code == "repetition":
        params: dict[str, Any] = {
            "distances": distances,
            "rounds": rounds,
            "criteria": criteria_profile,
        }
    else:
        params = {"distance": distance, "rounds": rounds, "criteria": criteria_profile}
    _execute_benchmark(f"qec_{code}", params, adapter, _resolve_seed(seed), shots, out, live=live)


@main.command("report")
@click.argument("inputs", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "-o",
    "--output",
    default="report.html",
    show_default=True,
    type=click.Path(dir_okay=False, path_type=Path),
)
@click.option(
    "--generated-at",
    default=None,
    help="RFC 3339 timestamp override for reproducible output (golden-file testing).",
)
def report(inputs: tuple[Path, ...], output: Path, generated_at: str | None) -> None:
    """Generate a self-contained static HTML report from QPR files/directories.

    Every input is verified first; unverifiable records are refused."""
    from datetime import datetime

    timestamp = None
    if generated_at is not None:
        try:
            timestamp = datetime.fromisoformat(generated_at)
        except ValueError as exc:
            raise click.BadParameter(f"--generated-at must be RFC 3339: {exc}") from exc
        if timestamp.tzinfo is None:
            raise click.BadParameter("--generated-at must carry a timezone offset")
    try:
        path = write_report(list(inputs), output, generated_at=timestamp)
    except ReportInputError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"wrote {path}")


@adapters.command("probe")
@click.argument("name")
@click.option(
    "--shots",
    default=100,
    show_default=True,
    type=click.IntRange(min=1),
    help="Shots for the smoke circuit.",
)
@click.option(
    "--live",
    is_flag=True,
    default=False,
    help="Allow the smoke circuit on a live adapter (credentials + cost gate still apply).",
)
@click.option("--device", default=None, help="Live device (IBM backend name / Braket ARN).")
def adapters_probe(name: str, shots: int, live: bool, device: str | None) -> None:
    """Instantiate an adapter, print capabilities + calibration, and run a
    1-qubit smoke circuit.

    For live adapters the smoke circuit is skipped unless --live is passed:
    a probe must never be the thing that spends quota."""
    resolved = ADAPTER_ALIASES.get(name, name)
    try:
        adapter = _build_adapter(name, None, live=live, device=device)
    except click.ClickException:
        raise
    except AdapterUnavailableError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    click.echo(f"adapter: {adapter.name} (version {adapter.adapter_version})")
    try:
        click.echo("capabilities:")
        click.echo(json.dumps(adapter.capabilities().model_dump(mode="json"), indent=2))
        calibration = adapter.calibration_snapshot()
    except LiveRefusedError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("calibration_snapshot:")
    click.echo(
        "null" if calibration is None else json.dumps(calibration.model_dump(mode="json"), indent=2)
    )
    if resolved in LIVE_ADAPTERS and not live:
        click.echo("smoke circuit: skipped (live adapter; pass --live to submit one job)")
        return
    start = time.perf_counter()
    try:
        counts = asyncio.run(_smoke_run(adapter, shots))
    except LiveRefusedError as exc:
        raise click.ClickException(str(exc)) from exc
    elapsed_ms = (time.perf_counter() - start) * 1000
    click.echo(f"smoke circuit ({shots} shots): counts={json.dumps(counts)}")
    click.echo(f"round-trip time: {elapsed_ms:.1f} ms")


@main.group()
def jobs() -> None:
    """Manage persisted live jobs."""


@jobs.command("resume")
@click.argument("handle_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--timeout",
    default=14_400.0,
    show_default=True,
    help="Seconds to keep polling before giving up (the handle stays resumable).",
)
def jobs_resume(handle_file: Path, out: Path, timeout: float) -> None:
    """Resume an interrupted live run from its handle file into a sealed QPR.

    Resuming polls and fetches results; it can never submit anything, so it
    needs credentials but not --live or the cost gate."""
    document = json.loads(handle_file.read_text(encoding="utf-8"))
    adapter_name = document.get("adapter")
    if not isinstance(adapter_name, str):
        raise click.ClickException(f"{handle_file}: not a veriqant-bench handle file")
    kwargs = document.get("adapter_kwargs", {})
    try:
        adapter = get_adapter(adapter_name, **kwargs)
    except AdapterUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        record = asyncio.run(resume_run(handle_file, adapter, timeout=timeout))
    except (ResumeError, LiveRefusedError) as exc:
        raise click.ClickException(str(exc)) from exc
    path = write_verified_qpr(record, out)
    click.echo(f"{path} {record.integrity.content_sha256}")


@main.group()
def limits() -> None:
    """Inspect live-execution spending limits and the local ledger."""


@limits.command("show")
def limits_show() -> None:
    """Print the effective limits, their source, and month-to-date spend."""
    from veriqant_bench.live import DEFAULT_LEDGER_PATH, SpendLedger, load_limits

    effective = load_limits()
    click.echo(f"limits source:        {effective.source}")
    click.echo(
        f"monetary cap:         {effective.monthly_monetary_cap} {effective.currency} / month"
    )
    click.echo(f"qpu-seconds cap:      {effective.monthly_qpu_seconds_cap:.1f} s / month")
    click.echo(f"allow_unknown_cost:   {effective.allow_unknown_cost}")
    ledger = SpendLedger()
    click.echo(f"ledger:               {DEFAULT_LEDGER_PATH}")
    totals = ledger.monthly_totals()
    click.echo(
        f"month to date:        {totals.monetary} {effective.currency}, "
        f"{totals.qpu_seconds:.1f} qpu-seconds ({totals.entries} entries)"
    )
    click.echo("note: the ledger is advisory client-side bookkeeping; keep provider-side")
    click.echo("billing alarms as the real backstop (docs/LIVE.md).")
