"""veriqore-bench command-line interface."""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

import click

from . import __version__
from .qpr import QPR_VERSION, verify_qpr_file

SCHEMA_RESOURCE = "qpr-0.1.0.schema.json"


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
