/**
 * Generates TypeScript types from the canonical QPR JSON Schema.
 *
 * The schema file is the single source of truth; the output file
 * src/generated/qpr.ts is committed so consumers don't need a codegen step,
 * and CI fails if it drifts from the schema.
 */
import { compileFromFile } from 'json-schema-to-typescript';
import { copyFile, mkdir, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const pkgRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const schemaPath = join(pkgRoot, 'schema', 'qpr-0.3.0.schema.json');
const outPath = join(pkgRoot, 'src', 'generated', 'qpr.ts');
const schemaCopyPath = join(pkgRoot, 'src', 'generated', 'qpr.schema.json');

const banner = `/* eslint-disable */
/**
 * AUTO-GENERATED from schema/qpr-0.3.0.schema.json — do not edit by hand.
 * Regenerate with: pnpm --filter @veriqant/schema generate
 */`;

const ts = await compileFromFile(schemaPath, {
  bannerComment: banner,
  additionalProperties: false,
  strictIndexSignatures: true,
  style: { singleQuote: true },
});

await mkdir(dirname(outPath), { recursive: true });
await writeFile(outPath, ts);
await copyFile(schemaPath, schemaCopyPath);
console.log(`wrote ${outPath}`);
console.log(`wrote ${schemaCopyPath}`);
