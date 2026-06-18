# Regulator Evidence Export Specification — v1

- **Status:** Draft (PRD-1)
- **Version:** v1
- **Last updated:** 2026-06-18
- **Reference implementation:** [`mrm/portal/`](../../mrm/portal/)
  (`export.py`, `merkle_proof.py`, `verify.py`)

## 1. Scope

This specification defines a **self-verifying evidence export package**
that a bank operator can generate for a regulator (APRA, Fed, ECB,
OSFI, EU AI Office). The examiner receives a single archive containing
all evidence, compliance reports, and decision records for the scoped
models and date range — plus a zero-dependency Python verification
script that cryptographically proves nothing was tampered with.

The export is a scoped, static projection of the existing
[Evidence Vault](evidence-vault-v1.md) and
[Decision Record](replay-record-v1.md) stores. It does not require a
running server, a database, or internet access. It runs anywhere
Python 3.9+ is installed.

**Delivery mechanism is infrastructure the bank already owns:**

- **AWS:** Upload to S3 with Object Lock, share via pre-signed URL
  (max 7 days, CloudTrail-logged)
- **Azure:** Upload to Blob Storage with immutability policy, share
  via SAS token (configurable expiry, IP-restricted)
- **GCP:** Upload to Cloud Storage, share via signed URL
- **Air-gapped:** Copy to encrypted USB or secure file transfer

No new web services. No new infrastructure approvals. No new
dependencies in the bank's environment.

Conformance keywords follow RFC 2119.

## 2. Threat model

### 2.1 Principals

| Principal | Trust level | Capability |
|---|---|---|
| Bank operator | Trusted | Creates export, sets scope, delivers archive |
| Regulator examiner | Semi-trusted | Receives archive, runs verification, reads evidence |
| External attacker | Untrusted | May intercept archive in transit |

### 2.2 Security goals

1. **Confidentiality:** The export MUST contain only artefacts within
   the operator-defined scope (models, date range, standards).
2. **Integrity:** The examiner MUST be able to independently verify
   that no artefact was modified after creation. Verification uses
   only `hashlib` and `json` from the Python standard library.
3. **Authenticity:** Every evidence packet carries a `content_hash`
   and `prior_packet_hash` forming a hash chain. Every daily Merkle
   root carries a cryptographic signature. Merkle inclusion proofs
   bind each packet to its signed root.
4. **Non-repudiation:** The signed Merkle roots prove the bank
   published these exact artefacts on the stated dates. The bank
   cannot later deny or alter them.
5. **Transit protection:** Delivery via pre-signed URL / SAS token
   over HTTPS. Air-gapped delivery via encrypted media.

### 2.3 Non-goals

- Real-time interactive querying (future: RiskAttest Cloud portal).
- Write access or evidence submission to regulator systems.
- Authentication of the examiner's identity (handled by delivery
  mechanism — pre-signed URL, SAS token, or physical handoff).
- ZKP selective disclosure (future: RiskAttest Cloud paid tier).

## 3. Export package structure

```
riskattest-export-{export_id}/
  RUNME.md                          # One-line instruction
  manifest.json                     # Scope, timestamp, file inventory with hashes
  verify_export.py                  # Zero-dependency verification script
  evidence/
    {model_name}/
      packets.json                  # Evidence packets in scope
      chain_verification.json       # Pre-computed chain walk results
  decisions/
    {model_name}/
      records.json                  # Decision records in scope (if included)
  compliance/
    {model_name}/
      {standard}_report.md          # Compliance report per standard
  merkle/
    {epoch}.root.json               # Signed daily Merkle roots in date range
    inclusion_proofs.json           # Merkle inclusion proof per evidence packet
  attestations/
    compliance_summary.json         # Signed aggregate attestation
    chain_integrity.json            # Signed chain integrity attestation
```

### 3.1 `RUNME.md`

```markdown
# RiskAttest Evidence Export — Verification

```bash
python verify_export.py
```

Requires Python 3.9+. No additional packages needed.
```

### 3.2 `manifest.json`

```json
{
  "export_id": "uuid-v4",
  "created_at": "2026-06-18T14:00:00Z",
  "created_by": "operator@bank.com.au",
  "riskattest_version": "0.1.0",
  "spec_version": "regulator-portal-v1",
  "scope": {
    "models": ["ccr_monte_carlo"],
    "date_range": {
      "start": "2025-07-01",
      "end": "2026-06-18"
    },
    "standards": ["cps230", "sr117"],
    "include_decision_records": true,
    "include_evidence_packets": true,
    "include_compliance_reports": true,
    "include_merkle_roots": true
  },
  "files": {
    "evidence/ccr_monte_carlo/packets.json": {
      "sha256": "abc123...",
      "size_bytes": 45230
    },
    "merkle/2026-06-15.root.json": {
      "sha256": "def456...",
      "size_bytes": 512
    }
  },
  "summary": {
    "total_evidence_packets": 12,
    "total_decision_records": 1847,
    "total_merkle_roots": 14,
    "models_included": 1,
    "standards_included": 2
  }
}
```

| Field | Type | Required |
|---|---|---|
| `export_id` | string (UUIDv4) | yes |
| `created_at` | string (RFC 3339, UTC `Z`) | yes |
| `created_by` | string | yes |
| `riskattest_version` | string | yes |
| `spec_version` | string (`"regulator-portal-v1"`) | yes |
| `scope` | object | yes |
| `files` | object (path -> {sha256, size_bytes}) | yes |
| `summary` | object | yes |

### 3.3 Scope object

| Field | Type | Required | Default |
|---|---|---|---|
| `models` | list of strings | yes | — |
| `date_range.start` | date `YYYY-MM-DD` | yes | — |
| `date_range.end` | date `YYYY-MM-DD` | yes | — |
| `standards` | list of standard IDs | no | all available |
| `include_decision_records` | boolean | no | `true` |
| `include_evidence_packets` | boolean | no | `true` |
| `include_compliance_reports` | boolean | no | `true` |
| `include_merkle_roots` | boolean | no | `true` |

## 4. Merkle inclusion proofs

### 4.1 Purpose

A Merkle inclusion proof lets the examiner verify that a specific
evidence packet was part of the signed daily Merkle root — without
needing access to any other packets from that day. This is the
mechanism that bridges the per-packet evidence chain to the signed
daily attestation.

### 4.2 Proof format

```json
{
  "packet_id": "uuid",
  "epoch": "2026-06-15",
  "leaf_hash": "hex-sha256",
  "root_hash": "hex-sha256",
  "leaf_index": 3,
  "tree_size": 12,
  "proof_hashes": [
    {"hash": "hex-sha256", "position": "right"},
    {"hash": "hex-sha256", "position": "left"},
    {"hash": "hex-sha256", "position": "right"}
  ]
}
```

| Field | Type | Required |
|---|---|---|
| `packet_id` | string (UUIDv4) | yes |
| `epoch` | date `YYYY-MM-DD` | yes |
| `leaf_hash` | hex SHA-256 | yes |
| `root_hash` | hex SHA-256 | yes |
| `leaf_index` | int (0-based) | yes |
| `tree_size` | int | yes |
| `proof_hashes` | list of {hash, position} | yes |

### 4.3 Verification algorithm

To verify a proof, the examiner computes:

```
current = leaf_hash
for step in proof_hashes:
    if step.position == "left":
        current = SHA-256(0x01 || bytes.fromhex(step.hash) || bytes.fromhex(current))
    else:
        current = SHA-256(0x01 || bytes.fromhex(current) || bytes.fromhex(step.hash))
assert current == root_hash
```

The `leaf_hash` is computed as:

```
leaf_hash = SHA-256(0x00 || bytes.fromhex(event_hash))
```

per [Evidence Vault §8.1](evidence-vault-v1.md).

### 4.4 `inclusion_proofs.json`

```json
{
  "proofs": [
    { "packet_id": "...", "epoch": "...", ... },
    { "packet_id": "...", "epoch": "...", ... }
  ]
}
```

## 5. Attestations

### 5.1 Compliance summary attestation

A signed JSON statement summarising the compliance posture for the
scoped models and date range.

```json
{
  "attestation_type": "compliance_summary",
  "export_id": "uuid",
  "timestamp": "2026-06-18T14:00:00Z",
  "models": {
    "ccr_monte_carlo": {
      "total_tests": 12,
      "tests_passed": 12,
      "tests_failed": 0,
      "all_passed": true,
      "latest_evidence_packet": "uuid",
      "latest_validation_date": "2026-06-15T14:30:00Z"
    }
  },
  "content_hash": "hex-sha256",
  "signature": "base64-encoded",
  "signer": "local"
}
```

The `content_hash` is `SHA-256(canonical_json(attestation - {content_hash, signature, signer}))`.

The `signature` is produced by the same `Signer` used for Merkle
roots (see [Evidence Vault §9](evidence-vault-v1.md)).

### 5.2 Chain integrity attestation

```json
{
  "attestation_type": "chain_integrity",
  "export_id": "uuid",
  "timestamp": "2026-06-18T14:00:00Z",
  "models": {
    "ccr_monte_carlo": {
      "chain_valid": true,
      "total_packets": 12,
      "first_packet_timestamp": "2025-07-01T00:00:00Z",
      "last_packet_timestamp": "2026-06-15T14:30:00Z",
      "genesis_packet_id": "uuid",
      "head_packet_id": "uuid"
    }
  },
  "content_hash": "hex-sha256",
  "signature": "base64-encoded",
  "signer": "local"
}
```

## 6. `verify_export.py`

The verification script MUST:

1. Have **zero external dependencies** — only Python 3.9+ standard
   library (`hashlib`, `json`, `os`, `sys`, `pathlib`).
2. Be **self-contained** in a single file.
3. Perform the following checks in order:

| Check | What it verifies | Pass condition |
|---|---|---|
| **Manifest integrity** | SHA-256 of every file matches `manifest.json` | All hashes match |
| **Evidence chain** | `content_hash` recomputation + `prior_packet_hash` linkage | All packets valid, chain unbroken |
| **Merkle inclusion** | Each packet's inclusion proof walks to the signed root | All proofs verify |
| **Root signatures** | Daily Merkle root signatures verify against signer | All roots pass |
| **Attestation integrity** | `content_hash` of attestation documents | All attestations valid |

4. Print a clear pass/fail summary:

```
RiskAttest Evidence Export Verification
=======================================
Export ID:    abc123-...
Created:      2026-06-18T14:00:00Z
Scope:        ccr_monte_carlo | 2025-07-01 to 2026-06-18

[PASS] Manifest integrity     (8/8 files verified)
[PASS] Evidence chain          (12 packets, chain valid)
[PASS] Merkle inclusion        (12/12 proofs verified)
[PASS] Root signatures         (14 roots, all signatures valid)
[PASS] Attestation integrity   (2/2 attestations valid)

RESULT: ALL CHECKS PASSED
```

5. Exit with code 0 if all checks pass, 1 if any check fails.

### 6.1 Limitations

The verification script supports `local` signer verification
(HMAC-SHA256) natively. For `gpg` and `age` signatures, the script
prints the signature and instructs the examiner to verify manually
with their own GPG/age installation. For `kms` signatures, the
script prints the KMS key ARN and instructs the examiner to verify
via AWS CLI.

## 7. CLI commands

```bash
# Generate evidence export package
mrm portal export \
  --models ccr_monte_carlo,credit_scorecard \
  --start 2025-07-01 \
  --end 2026-06-18 \
  --standards cps230,sr117 \
  --output /tmp/riskattest-export.zip

# Generate and upload to S3 with pre-signed URL
mrm portal export \
  --models ccr_monte_carlo \
  --start 2025-07-01 \
  --end 2026-06-18 \
  --upload s3://bank-evidence-exports/ \
  --presign-expiry 7d

# List past exports
mrm portal list-exports
```

## 8. Configuration

Export configuration in `mrm_project.yml`:

```yaml
portal:
  # Signer for attestations (reuses evidence vault signer)
  signer:
    name: local             # local | gpg | age | kms
    # GPG: key_id: "ABCD1234"
    # KMS: key_arn: "arn:aws:kms:..."

  # S3 upload settings (optional)
  upload:
    bucket: bank-evidence-exports
    prefix: regulator-exports/
    region: ap-southeast-2

  # Export defaults
  defaults:
    include_decision_records: true
    include_compliance_reports: true
    presign_expiry_days: 7
```

Credentials MUST be read from environment variables. They MUST NOT
appear in YAML configuration, CLI output, or log files.

## 9. Bank IT deployment considerations

### 9.1 No new infrastructure

The export is a CLI command that reads from existing evidence/replay
backends and writes a ZIP file. Delivery piggybacks on the bank's
existing cloud storage (S3, Blob, GCS) with native time-limited
sharing (pre-signed URLs, SAS tokens, signed URLs).

### 9.2 Audit trail

Cloud storage access logging (CloudTrail, Azure Monitor, GCS audit
logs) automatically records every download of the export by the
examiner. No custom audit log infrastructure is needed.

### 9.3 Data residency

The export is generated within the bank's environment. Data leaves
the bank's infrastructure only when the examiner downloads via the
pre-signed URL. Banks with data residency requirements can restrict
the S3 bucket to specific regions.

### 9.4 Air-gapped examinations

For on-site examinations in air-gapped environments:

```bash
mrm portal export --models ccr_monte_carlo --output /media/usb/export.zip
```

The examiner copies the ZIP and runs `python verify_export.py` on
their own machine. No network access required.

## 10. Future: RiskAttest Cloud portal (v2)

This specification covers the static export (Option A). The
interactive portal (Option C) is planned for RiskAttest Cloud and
will include:

- Real-time queryable API (FastAPI, JWT-scoped sessions)
- Interactive chain verification in browser
- ZKP selective disclosure (prove compliance metrics without
  revealing proprietary test data, using Pedersen commitments)
- SIEM integration (Splunk, Sentinel, QRadar) for access audit logs

The static export remains the foundation — the interactive portal
is a live view of the same underlying data.

## 11. Conformance

Conforming implementations MUST:

1. Include a `manifest.json` with SHA-256 hash of every file (§3.2).
2. Include `verify_export.py` with zero external dependencies (§6).
3. Include Merkle inclusion proofs for every evidence packet (§4).
4. Sign attestations with the configured Signer (§5).
5. Enforce scope — export MUST NOT include artefacts outside the
   operator-specified models, date range, and standards (§3.3).
6. Never modify source evidence data during export.

Test vectors are published under
[`test-vectors/portal/`](test-vectors/portal/).

## 12. Changelog

| Version | Date | Change |
|---|---|---|
| v1 (PRD-1) | 2026-06-18 | Initial draft. Static export with Merkle inclusion proofs. |
