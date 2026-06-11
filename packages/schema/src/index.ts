import { Ajv2020, type ErrorObject, type ValidateFunction } from 'ajv/dist/2020.js';
import addFormatsExport from 'ajv-formats';

// ajv-formats ships CJS with a default-export .d.ts; under NodeNext the
// callable lives on .default at type level but is the module itself at runtime.
const addFormats = addFormatsExport as unknown as typeof addFormatsExport.default;
import qprSchema010 from './generated/qpr.schema.json' with { type: 'json' };
import type { QuantumPerformanceRecord } from './generated/qpr.js';

export type * from './generated/qpr.js';

/** Current QPR schema version shipped by this package. */
export const QPR_VERSION = '0.3.0';

/** QPR major versions this package can validate. */
export const SUPPORTED_QPR_MAJOR_VERSIONS = [0] as const;

/** The canonical QPR JSON Schema document. */
export const qprSchema = qprSchema010;

export interface QprValidationResult {
  valid: boolean;
  errors: ErrorObject[];
}

let compiled: ValidateFunction<QuantumPerformanceRecord> | undefined;

function validator(): ValidateFunction<QuantumPerformanceRecord> {
  if (!compiled) {
    const ajv = new Ajv2020({ allErrors: true, strict: true });
    addFormats(ajv);
    compiled = ajv.compile<QuantumPerformanceRecord>(qprSchema010);
  }
  return compiled;
}

/** Validates an arbitrary value against the QPR schema. */
export function validateQpr(value: unknown): QprValidationResult {
  const validate = validator();
  const valid = validate(value);
  return { valid, errors: validate.errors ? [...validate.errors] : [] };
}

/** Type guard built on the Ajv validator. */
export function isQpr(value: unknown): value is QuantumPerformanceRecord {
  return validateQpr(value).valid;
}

/**
 * Extracts the major version from a record's qpr_version, or null if the
 * field is missing/malformed. Ingestion rejects unsupported majors.
 */
export function qprMajorVersion(value: unknown): number | null {
  if (typeof value !== 'object' || value === null) return null;
  const version = (value as Record<string, unknown>)['qpr_version'];
  if (typeof version !== 'string') return null;
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(version);
  return match ? Number(match[1]) : null;
}
