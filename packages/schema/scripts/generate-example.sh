#!/usr/bin/env bash
# Regenerates the committed cross-language golden example QPR from the
# deterministic reference producer in veriqant-bench.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
OUT="$ROOT/packages/schema/examples/qpr-rb-example.json"

cd "$ROOT/packages/bench"
uv run python -c "
from pathlib import Path

from veriqant_bench.qpr import dumps_qpr
from veriqant_bench.qpr.example import example_record

Path('$OUT').write_text(dumps_qpr(example_record()), encoding='utf-8')
print('wrote $OUT')
"
