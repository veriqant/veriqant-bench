import { describe, expect, it } from 'vitest';
import {
  QPR_VERSION,
  qprMajorVersion,
  qprSchema,
  isQpr,
  validateQpr,
  type QuantumPerformanceRecord,
} from '../src/index.js';
import goldenExample from '../examples/qpr-rb-example.json' with { type: 'json' };

function clone(): Record<string, unknown> {
  return structuredClone(goldenExample) as Record<string, unknown>;
}

describe('QPR schema package', () => {
  it('exposes a schema whose $id matches QPR_VERSION', () => {
    expect(qprSchema.$id).toContain(QPR_VERSION);
  });

  it('validates the golden example produced by the Python SDK', () => {
    const result = validateQpr(goldenExample);
    expect(result.errors).toEqual([]);
    expect(result.valid).toBe(true);
  });

  it('types the golden example via the generated TS types', () => {
    expect(isQpr(goldenExample)).toBe(true);
    if (!isQpr(goldenExample)) return;
    // Compile-time check: generated types describe the validated value.
    const record: QuantumPerformanceRecord = goldenExample;
    expect(record.qpr_version).toBe(QPR_VERSION);
    expect(record.circuits[0].qasm3_sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(record.results.metrics[0].statistics.confidence_level).toBeGreaterThan(0);
  });

  it('rejects a record with a missing required member', () => {
    const broken = clone();
    delete broken['integrity'];
    expect(validateQpr(broken).valid).toBe(false);
  });

  it('rejects unknown top-level properties', () => {
    const broken = clone();
    broken['vendor_extension'] = true;
    expect(validateQpr(broken).valid).toBe(false);
  });

  it('rejects a metric without confidence interval statistics', () => {
    const broken = clone();
    const metrics = (broken as { results: { metrics: Record<string, unknown>[] } }).results
      .metrics;
    delete metrics[0]!['statistics'];
    const result = validateQpr(broken);
    expect(result.valid).toBe(false);
    expect(JSON.stringify(result.errors)).toContain('statistics');
  });

  it('rejects malformed semver and bitstring keys', () => {
    const badVersion = clone();
    badVersion['qpr_version'] = 'v1.0';
    expect(validateQpr(badVersion).valid).toBe(false);

    const badCounts = clone();
    (badCounts as { results: { raw: { counts: Record<string, number> }[] } }).results.raw[0]!.counts[
      '012'
    ] = 1;
    expect(validateQpr(badCounts).valid).toBe(false);
  });

  it('extracts major versions for ingestion gating', () => {
    expect(qprMajorVersion(goldenExample)).toBe(0);
    expect(qprMajorVersion({ qpr_version: '12.3.4' })).toBe(12);
    expect(qprMajorVersion({ qpr_version: 'nonsense' })).toBeNull();
    expect(qprMajorVersion({})).toBeNull();
    expect(qprMajorVersion(null)).toBeNull();
  });
});
