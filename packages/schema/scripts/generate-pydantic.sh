#!/usr/bin/env bash
# Generates Pydantic v2 models for veriqant-bench from the canonical QPR JSON
# Schema, and bundles a copy of the schema as package data (stable filename
# qpr.schema.json regardless of schema version).
# The output is committed; CI fails if it drifts from the schema.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCHEMA="$ROOT/packages/schema/schema/qpr-0.3.0.schema.json"
BENCH="$ROOT/packages/bench"
OUT="$BENCH/src/veriqant_bench/qpr/_generated.py"

cd "$BENCH"
uv run datamodel-codegen \
  --input "$SCHEMA" \
  --input-file-type jsonschema \
  --output "$OUT" \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --use-annotated \
  --field-constraints \
  --use-standard-collections \
  --use-union-operator \
  --strict-nullable \
  --use-schema-description \
  --use-field-description \
  --use-double-quotes \
  --enum-field-as-literal all \
  --disable-timestamp \
  --custom-file-header "# AUTO-GENERATED from packages/schema/schema/$(basename "$SCHEMA") — do not edit.
# Regenerate with: packages/schema/scripts/generate-pydantic.sh"

uv run ruff format "$OUT" >/dev/null
uv run ruff check --fix --quiet "$OUT" || true

cp "$SCHEMA" "$BENCH/src/veriqant_bench/qpr/qpr.schema.json"
echo "wrote $OUT"
echo "wrote $BENCH/src/veriqant_bench/qpr/qpr.schema.json"
