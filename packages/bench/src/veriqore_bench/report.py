"""Static HTML report generation from sealed QPR files.

One self-contained HTML document: inline CSS, inline SVG charts, zero
external requests — it must open offline and survive being emailed. This is
intended for static hosting of demonstration reports, deliberately
primitive; richer interactive surfaces are a separate concern.

Every input file is verified first; unverifiable records are refused with a
per-file explanation. Output is deterministic given identical inputs and a
fixed generated-at timestamp. Unreliable metrics are visibly badged.
"""

from __future__ import annotations

import html
import math
from datetime import UTC, datetime
from pathlib import Path

from veriqore_bench import __version__
from veriqore_bench.qpr import QuantumPerformanceRecord, load_qpr, verify_qpr_file
from veriqore_bench.qpr._generated import Metric

CHART_WIDTH = 480
CHART_HEIGHT = 220
MARGIN = 40


class ReportInputError(ValueError):
    """One or more input files failed verification or could not be read."""


def collect_qpr_files(inputs: list[Path]) -> list[Path]:
    """Expand files/directories into a sorted, de-duplicated list of QPR files."""
    files: set[Path] = set()
    for path in inputs:
        if path.is_dir():
            files.update(path.glob("*.qpr.json"))
        else:
            files.add(path)
    return sorted(files)


def load_verified_records(
    paths: list[Path],
) -> list[tuple[Path, QuantumPerformanceRecord]]:
    """Verify every file, then load it. Refuses the whole set on any failure
    so a report can never silently include an unverifiable record."""
    failures: list[str] = []
    records: list[tuple[Path, QuantumPerformanceRecord]] = []
    for path in paths:
        report = verify_qpr_file(path)
        if not report.ok:
            errors = "; ".join(str(issue) for issue in report.issues if issue.severity == "error")
            failures.append(f"{path}: {errors}")
            continue
        records.append((path, load_qpr(path)))
    if failures:
        raise ReportInputError(
            "refusing to report on unverifiable records:\n" + "\n".join(failures)
        )
    if not records:
        raise ReportInputError("no QPR files found in the given inputs")
    records.sort(key=lambda item: (item[1].benchmark.id, str(item[1].record_id)))
    return records


def render_report(
    records: list[tuple[Path, QuantumPerformanceRecord]],
    *,
    generated_at: datetime,
    tool_version: str = __version__,
) -> str:
    """Render the full self-contained HTML document."""
    rows = "\n".join(_summary_row(record) for _, record in records)
    sections = "\n".join(_record_section(record) for _, record in records)
    timestamp = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Veriqore benchmark report</title>
<style>
body {{ font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 60rem; color: #1a1a2e; }}
h1 {{ font-size: 1.5rem; }} h2 {{ font-size: 1.15rem; margin-top: 2.5rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: 0.85rem; }}
th, td {{ text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #ddd; }}
th {{ background: #f4f4f8; }}
code {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.8rem; }}
.meta {{ color: #666; font-size: 0.8rem; }}
.badge {{ display: inline-block; padding: 0.1rem 0.45rem; border-radius: 0.6rem;
         font-size: 0.72rem; font-weight: 600; }}
.badge.ok {{ background: #e2f5e8; color: #176635; }}
.badge.warn {{ background: #fdf3d7; color: #7a5d0c; }}
.badge.bad {{ background: #fde2e2; color: #8f1d1d; }}
.badge.na {{ background: #ececf1; color: #555; }}
.issues {{ color: #8f1d1d; font-size: 0.75rem; }}
.watermark {{ color: #7a5d0c; font-size: 0.78rem; font-weight: 600; }}
svg {{ background: #fbfbfd; border: 1px solid #e5e5ee; margin-top: 0.6rem; }}
</style>
</head>
<body>
<h1>Veriqore benchmark report</h1>
<p class="meta">generated {html.escape(timestamp)} · veriqore-bench {html.escape(tool_version)}
 · {len(records)} record(s) · all records verified</p>
<table>
<thead><tr><th>benchmark</th><th>adapter / device</th><th>key metric</th>
<th>value (95% CI)</th><th>quality</th><th>record</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>
{sections}
</body>
</html>
"""


def write_report(
    inputs: list[Path],
    output: Path,
    *,
    generated_at: datetime | None = None,
    tool_version: str = __version__,
) -> Path:
    records = load_verified_records(collect_qpr_files(inputs))
    document = render_report(
        records,
        generated_at=generated_at or datetime.now(tz=UTC),
        tool_version=tool_version,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return output


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def _quality_badge(metric: Metric) -> str:
    if metric.quality is None:
        return '<span class="badge ok">no diagnostics</span>'
    issues = ", ".join(metric.quality.issues or [])
    if not metric.quality.reliable:
        return (
            f'<span class="badge bad">UNRELIABLE</span> '
            f'<span class="issues">{html.escape(issues)}</span>'
        )
    if issues:
        return (
            f'<span class="badge warn">issues</span> '
            f'<span class="issues">{html.escape(issues)}</span>'
        )
    return '<span class="badge ok">reliable</span>'


def _summary_row(record: QuantumPerformanceRecord) -> str:
    primary = record.results.metrics[0]
    statistics = primary.statistics
    value = f"{_fmt(primary.value)} [{_fmt(statistics.ci_lower)}, {_fmt(statistics.ci_upper)}]"
    device = f"{record.provider.adapter} / {record.device.name}"
    if record.device.simulator:
        device += " (simulator)"
    return (
        "<tr>"
        f"<td>{html.escape(record.benchmark.id)}</td>"
        f"<td>{html.escape(device)}</td>"
        f"<td>{html.escape(primary.name)}</td>"
        f"<td>{html.escape(value)}</td>"
        f"<td>{_quality_badge(primary)}</td>"
        f"<td><code>{html.escape(record.integrity.content_sha256[:12])}</code></td>"
        "</tr>"
    )


def _record_section(record: QuantumPerformanceRecord) -> str:
    metric_rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(metric.name)}</td>"
        f"<td>{_fmt(metric.value)}{f' {html.escape(metric.unit)}' if metric.unit else ''}</td>"
        f"<td>[{_fmt(metric.statistics.ci_lower)}, {_fmt(metric.statistics.ci_upper)}]</td>"
        f"<td>{metric.statistics.sample_size}</td>"
        f"<td>{_quality_badge(metric)}</td>"
        "</tr>"
        for metric in record.results.metrics
    )
    chart = _chart_for(record)
    scorecard = _criteria_scorecard(record)
    seed = record.execution.seed
    return f"""
<h2>{html.escape(record.benchmark.display_name or record.benchmark.id)}</h2>
<p class="meta">adapter {html.escape(record.provider.adapter)} · device
 {html.escape(record.device.name)} · seed {seed} · shots {record.execution.shots}
 · suite {html.escape(record.benchmark.suite_version)}
 · <code>{html.escape(record.integrity.content_sha256)}</code></p>
<table>
<thead><tr><th>metric</th><th>value</th><th>95% CI</th><th>samples</th><th>quality</th></tr></thead>
<tbody>
{metric_rows}
</tbody>
</table>
{scorecard}
{chart}
"""


_VERDICT_BADGE = {
    "pass": '<span class="badge ok">pass</span>',
    "fail": '<span class="badge bad">fail</span>',
    "not_evaluable": '<span class="badge na">not evaluable</span>',
}


def _criteria_scorecard(record: QuantumPerformanceRecord) -> str:
    analysis = record.results.analysis or {}
    criteria = analysis.get("criteria")
    if not isinstance(criteria, dict):
        return ""
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(verdict.get('criterion', '')))}</td>"
        f"<td>{_VERDICT_BADGE.get(str(verdict.get('status')), '')}</td>"
        f"<td>{html.escape(str(verdict.get('reason') or ''))}</td>"
        "</tr>"
        for verdict in criteria.get("verdicts", [])
    )
    watermark = (
        '<p class="watermark">⚠ verdicts derived from a simulated noise model — '
        "this is a demonstration of the criteria machinery, not a hardware claim</p>"
        if criteria.get("simulated")
        else ""
    )
    return f"""
<h3>criteria scorecard: {html.escape(str(criteria.get("profile", "")))}
 v{html.escape(str(criteria.get("version", "")))}</h3>
<p class="meta">{html.escape(str(criteria.get("citation", "")))}</p>
{watermark}
<table>
<thead><tr><th>criterion</th><th>verdict</th><th>reason</th></tr></thead>
<tbody>
{rows}
</tbody>
</table>"""


def _chart_for(record: QuantumPerformanceRecord) -> str:
    analysis = record.results.analysis or {}
    benchmark_id = record.benchmark.id
    if benchmark_id.startswith("rb_") and "survival_means" in analysis:
        points = sorted(
            (float(length), float(mean)) for length, mean in analysis["survival_means"].items()
        )
        return _line_chart(points, "sequence length", "survival probability", y_max=1.0)
    if benchmark_id == "mirror_circuits" and "per_depth" in analysis:
        points = sorted(
            (float(depth), float(values["mean_polarization"]))
            for depth, values in analysis["per_depth"].items()
        )
        return _line_chart(points, "depth", "polarization", y_max=1.0)
    if benchmark_id == "quantum_volume" and "per_width" in analysis:
        points = sorted(
            (float(width), float(values["mean_heavy_output_probability"]))
            for width, values in analysis["per_width"].items()
        )
        return _line_chart(
            points,
            "width",
            "heavy-output probability",
            y_max=1.0,
            threshold=2.0 / 3.0,
            threshold_label="2/3 pass threshold",
        )
    if benchmark_id == "throughput" and "batches" in analysis:
        points = [
            (float(batch["batch"]), float(batch["round_trip_seconds"]))
            for batch in analysis["batches"]
        ]
        return _line_chart(points, "batch", "round trip (s)")
    if benchmark_id == "qec_repetition_memory" and "per_distance" in analysis:
        points = sorted(
            (
                float(distance),
                math.log10(max(float(detail["eps"]["value"]), 1e-12)),
            )
            for distance, detail in analysis["per_distance"].items()
        )
        lambda_note = ", ".join(
            f"Λ(d{step['from_distance']}→d{step['to_distance']}) = {_fmt(step['value'])}"
            for step in analysis.get("lambda_steps", [])
        )
        chart = _line_chart(points, "code distance", "log10(logical error / round)")
        if lambda_note:
            chart += f'\n<p class="meta">{html.escape(lambda_note)}</p>'
        return chart
    return ""


def _line_chart(
    points: list[tuple[float, float]],
    x_label: str,
    y_label: str,
    *,
    y_max: float | None = None,
    threshold: float | None = None,
    threshold_label: str = "",
) -> str:
    if not points:
        return ""
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x_lo, x_hi = min(xs), max(xs)
    y_lo = min(0.0, min(ys))
    y_hi = y_max if y_max is not None else max(ys) * 1.1 or 1.0
    if x_hi == x_lo:
        x_hi = x_lo + 1.0
    if y_hi == y_lo:
        y_hi = y_lo + 1.0

    def sx(x: float) -> float:
        return MARGIN + (x - x_lo) / (x_hi - x_lo) * (CHART_WIDTH - 2 * MARGIN)

    def sy(y: float) -> float:
        return CHART_HEIGHT - MARGIN - (y - y_lo) / (y_hi - y_lo) * (CHART_HEIGHT - 2 * MARGIN)

    polyline = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in points)
    dots = "\n".join(
        f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3" fill="#3b4cc0"/>' for x, y in points
    )
    threshold_svg = ""
    if threshold is not None:
        ty = sy(threshold)
        threshold_svg = (
            f'<line x1="{MARGIN}" y1="{ty:.1f}" x2="{CHART_WIDTH - MARGIN}" y2="{ty:.1f}"'
            ' stroke="#c03b3b" stroke-dasharray="5,4"/>'
            f'<text x="{CHART_WIDTH - MARGIN}" y="{ty - 5:.1f}" text-anchor="end"'
            f' font-size="10" fill="#c03b3b">{html.escape(threshold_label)}</text>'
        )
    axis_color = "#888"
    return f"""<svg width="{CHART_WIDTH}" height="{CHART_HEIGHT}" role="img"
 aria-label="{html.escape(y_label)} vs {html.escape(x_label)}">
<line x1="{MARGIN}" y1="{CHART_HEIGHT - MARGIN}" x2="{CHART_WIDTH - MARGIN}"
 y2="{CHART_HEIGHT - MARGIN}" stroke="{axis_color}"/>
<line x1="{MARGIN}" y1="{MARGIN}" x2="{MARGIN}" y2="{CHART_HEIGHT - MARGIN}"
 stroke="{axis_color}"/>
<text x="{CHART_WIDTH / 2:.0f}" y="{CHART_HEIGHT - 8}" text-anchor="middle"
 font-size="11" fill="#444">{html.escape(x_label)}</text>
<text x="12" y="{CHART_HEIGHT / 2:.0f}" text-anchor="middle" font-size="11" fill="#444"
 transform="rotate(-90 12 {CHART_HEIGHT / 2:.0f})">{html.escape(y_label)}</text>
<text x="{MARGIN - 6}" y="{sy(y_lo) + 4:.1f}" text-anchor="end" font-size="10"
 fill="#666">{_fmt(y_lo)}</text>
<text x="{MARGIN - 6}" y="{sy(y_hi) + 4:.1f}" text-anchor="end" font-size="10"
 fill="#666">{_fmt(y_hi)}</text>
<text x="{sx(x_lo):.1f}" y="{CHART_HEIGHT - MARGIN + 14}" text-anchor="middle"
 font-size="10" fill="#666">{_fmt(x_lo)}</text>
<text x="{sx(x_hi):.1f}" y="{CHART_HEIGHT - MARGIN + 14}" text-anchor="middle"
 font-size="10" fill="#666">{_fmt(x_hi)}</text>
{threshold_svg}
<polyline points="{polyline}" fill="none" stroke="#3b4cc0" stroke-width="1.5"/>
{dots}
</svg>"""
