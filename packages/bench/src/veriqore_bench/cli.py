"""veriqore-bench command-line interface."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from importlib.resources import files
from pathlib import Path

import click

from . import __version__
from .adapters import AdapterUnavailableError, JobSpec, QPUAdapter, list_adapters
from .adapters import get as get_adapter
from .qpr import QPR_VERSION, verify_qpr_file

SCHEMA_RESOURCE = "qpr-0.1.0.schema.json"

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
