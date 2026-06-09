#!/usr/bin/env bash
# Generates Pydantic v2 models for veriqore-bench from the canonical QPR JSON
# Schema, and bundles a copy of the schema as package data.
# The output is committed; CI fails if it drifts from the schema.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCHEMA="$ROOT/packages/schema/schema/qpr-0.1.0.schema.json"
BENCH="$ROOT/packages/bench"
OUT="$BENCH/src/veriqore_bench/qpr/_generated.py"

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
  --custom-file-header "# AUTO-GENERATED from packages/schema/schema/qpr-0.1.0.schema.json — do not edit.
# Regenerate with: packages/schema/scripts/generate-pydantic.sh"

uv run ruff format "$OUT" >/dev/null
uv run ruff check --fix --quiet "$OUT" || true

cp "$SCHEMA" "$BENCH/src/veriqore_bench/qpr/qpr-0.1.0.schema.json"
echo "wrote $OUT"
echo "wrote $BENCH/src/veriqore_bench/qpr/qpr-0.1.0.schema.json"
