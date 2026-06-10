# Security policy

## Reporting a vulnerability

Please report suspected vulnerabilities privately to
**security@veriqant.dev**. Do not open a public issue for security reports.

We will acknowledge within 72 hours and aim to provide a fix or mitigation
plan within 30 days. Coordinated disclosure is appreciated; we will credit
reporters in release notes unless you prefer otherwise.

## Scope notes

- QPR integrity: the content seal (`integrity.content_sha256`) is
  tamper-evidence, not access control. Ed25519 signatures
  (`veriqant-bench[signing]`) prove a record was sealed by the holder of a
  key; key trust is the consumer's policy decision.
- Reports that a *sealed and signed* record can be altered without
  detection by `veriqant-bench verify` are treated as highest severity.

## Supported versions

Only the latest minor release receives security fixes.
