"""veriqore-bench command-line interface."""

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
from .adapters import AdapterUnavailableError, JobSpec, NoiseSpec, QPUAdapter, list_adapters
from .adapters import get as get_adapter
from .benchmarks import (
    BenchmarkUnavailableError,
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
@click.version_option(version=__version__, prog_name="veriqore-bench")
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
    text = files("veriqore_bench.qpr").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    click.echo(text, nl=False)


@main.command()
def version() -> None:
    """Print package and QPR schema versions."""
    click.echo(f"veriqore-bench {__version__}")
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


ADAPTER_ALIASES = {"aer": "aer_simulator", "braket": "braket_local"}


def _int_list(_ctx: click.Context, _param: click.Parameter, value: str) -> list[int]:
    try:
        return [int(item) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise click.BadParameter(f"expected comma-separated integers, got {value!r}") from exc


def _build_adapter(name: str, noise_file: Path | None) -> QPUAdapter:
    resolved = ADAPTER_ALIASES.get(name, name)
    kwargs: dict[str, Any] = {}
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
    except AdapterUnavailableError as exc:
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
) -> None:
    try:
        benchmark = get_benchmark(benchmark_name)
    except BenchmarkUnavailableError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        validated = benchmark.params_model.model_validate(params)
    except pydantic.ValidationError as exc:
        raise click.ClickException(f"invalid parameters: {exc}") from exc
    record = asyncio.run(run_benchmark(benchmark, adapter, validated, seed=seed, shots=shots))
    path = write_verified_qpr(record, out)
    click.echo(f"{path} {record.integrity.content_sha256}")


@main.group()
def run() -> None:
    """Run benchmarks and emit sealed, self-verified QPRs."""


@run.command("rb")
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
@click.option("--shots", default=256, show_default=True)
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
    adapter = _build_adapter(adapter_name, noise_file)
    _execute_benchmark(
        "rb",
        {"qubits": qubits, "lengths": lengths, "samples_per_length": samples},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
    )


@run.command("mirror")
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
@click.option("--shots", default=256, show_default=True)
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
    adapter = _build_adapter(adapter_name, noise_file)
    _execute_benchmark(
        "mirror",
        {"qubits": qubits, "depths": depths, "samples_per_depth": samples},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
    )


@run.command("qv")
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
@click.option("--shots", default=256, show_default=True)
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
    adapter_name: str,
    widths: list[int],
    circuits: int,
    shots: int,
    seed: int | None,
    noise_file: Path | None,
    out: Path,
) -> None:
    """Quantum volume: heavy-output probability per width, 2-sigma pass rule."""
    adapter = _build_adapter(adapter_name, noise_file)
    _execute_benchmark(
        "qv",
        {"widths": widths, "circuits_per_width": circuits},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
    )


@run.command("throughput")
@click.option("--adapter", "adapter_name", default="aer_simulator", show_default=True)
@click.option("--batches", default=5, show_default=True, help="Sequential timed batches (R).")
@click.option("--batch-size", default=10, show_default=True, help="Circuits per batch (B).")
@click.option("--width", default=2, show_default=True, help="Template circuit width.")
@click.option("--depth", default=4, show_default=True, help="Template mirror half-depth.")
@click.option("--shots", default=256, show_default=True)
@click.option(
    "--seed", type=int, default=None, help="Master seed; generated and printed when omitted."
)
@click.option(
    "--out", default="results", show_default=True, type=click.Path(file_okay=False, path_type=Path)
)
def run_throughput(
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
    flagged accordingly."""
    adapter = _build_adapter(adapter_name, None)
    _execute_benchmark(
        "throughput",
        {"batches": batches, "batch_size": batch_size, "width": width, "depth": depth},
        adapter,
        _resolve_seed(seed),
        shots,
        out,
    )


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
@click.option("--shots", default=100, show_default=True, help="Shots for the smoke circuit.")
def adapters_probe(name: str, shots: int) -> None:
    """Instantiate an adapter, print capabilities + calibration, and run a
    1-qubit smoke circuit."""
    try:
        adapter = get_adapter(name)
    except AdapterUnavailableError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)
    click.echo(f"adapter: {adapter.name} (version {adapter.adapter_version})")
    click.echo("capabilities:")
    click.echo(json.dumps(adapter.capabilities().model_dump(mode="json"), indent=2))
    calibration = adapter.calibration_snapshot()
    click.echo("calibration_snapshot:")
    click.echo(
        "null" if calibration is None else json.dumps(calibration.model_dump(mode="json"), indent=2)
    )
    start = time.perf_counter()
    counts = asyncio.run(_smoke_run(adapter, shots))
    elapsed_ms = (time.perf_counter() - start) * 1000
    click.echo(f"smoke circuit ({shots} shots): counts={json.dumps(counts)}")
    click.echo(f"round-trip time: {elapsed_ms:.1f} ms")
