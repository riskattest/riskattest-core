"""Regulator evidence export packager.

Generates a self-verifying ZIP archive containing evidence packets,
decision records, compliance reports, Merkle roots, inclusion proofs,
signed attestations, and a zero-dependency verification script.

See ``docs/spec/regulator-portal-v1.md`` for the full specification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mrm.portal.merkle_proof import InclusionProof, build_inclusion_proof

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical encoding (matches evidence/replay specs)
# ---------------------------------------------------------------------------

def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_str(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Export scope
# ---------------------------------------------------------------------------

@dataclass
class ExportScope:
    """Defines what the export includes."""

    models: List[str]
    date_start: str  # YYYY-MM-DD
    date_end: str  # YYYY-MM-DD
    standards: Optional[List[str]] = None  # None = all available
    include_decision_records: bool = True
    include_evidence_packets: bool = True
    include_compliance_reports: bool = True
    include_merkle_roots: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "models": self.models,
            "date_range": {
                "start": self.date_start,
                "end": self.date_end,
            },
            "standards": self.standards or "*",
            "include_decision_records": self.include_decision_records,
            "include_evidence_packets": self.include_evidence_packets,
            "include_compliance_reports": self.include_compliance_reports,
            "include_merkle_roots": self.include_merkle_roots,
        }


# ---------------------------------------------------------------------------
# Attestation
# ---------------------------------------------------------------------------

@dataclass
class Attestation:
    """A signed aggregate attestation."""

    attestation_type: str
    export_id: str
    timestamp: str
    models: Dict[str, Any]
    content_hash: Optional[str] = None
    signature: Optional[str] = None
    signer: Optional[str] = None

    def compute_content_hash(self) -> str:
        body = self.to_signable_dict()
        return _sha256_str(_canonical_json(body))

    def to_signable_dict(self) -> Dict[str, Any]:
        """Dict for hashing — excludes content_hash, signature, signer."""
        return {
            "attestation_type": self.attestation_type,
            "export_id": self.export_id,
            "timestamp": self.timestamp,
            "models": self.models,
        }

    def to_dict(self) -> Dict[str, Any]:
        d = self.to_signable_dict()
        d["content_hash"] = self.content_hash
        d["signature"] = self.signature
        d["signer"] = self.signer
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attestation":
        return cls(**data)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@dataclass
class ExportManifest:
    """Top-level manifest for the export archive."""

    export_id: str
    created_at: str
    created_by: str
    riskattest_version: str
    spec_version: str = "regulator-portal-v1"
    scope: Optional[Dict[str, Any]] = None
    files: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "export_id": self.export_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "riskattest_version": self.riskattest_version,
            "spec_version": self.spec_version,
            "scope": self.scope,
            "files": self.files,
            "summary": self.summary,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExportManifest":
        return cls(
            export_id=data["export_id"],
            created_at=data["created_at"],
            created_by=data["created_by"],
            riskattest_version=data["riskattest_version"],
            spec_version=data.get("spec_version", "regulator-portal-v1"),
            scope=data.get("scope"),
            files=data.get("files", {}),
            summary=data.get("summary", {}),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ExportManifest":
        return cls.from_dict(json.loads(json_str))


# ---------------------------------------------------------------------------
# Export builder
# ---------------------------------------------------------------------------

class ExportBuilder:
    """Builds a regulator evidence export package.

    Usage::

        builder = ExportBuilder(
            scope=ExportScope(models=["ccr_monte_carlo"], ...),
            created_by="operator@bank.com.au",
        )
        builder.add_evidence_packets("ccr_monte_carlo", packets)
        builder.add_compliance_report("ccr_monte_carlo", "cps230", report_md)
        builder.add_merkle_roots(roots)
        builder.add_inclusion_proofs(proofs)
        builder.build("/tmp/export.zip")
    """

    def __init__(
        self,
        scope: ExportScope,
        created_by: str,
        riskattest_version: str = "0.1.0",
        signer: Optional[Any] = None,
    ):
        self.export_id = str(uuid.uuid4())
        self.scope = scope
        self.created_by = created_by
        self.riskattest_version = riskattest_version
        self.signer = signer

        # Internal file registry: path -> content (bytes).
        self._files: Dict[str, bytes] = {}

        # Counters for summary.
        self._n_evidence_packets = 0
        self._n_decision_records = 0
        self._n_merkle_roots = 0
        self._models_included: set = set()
        self._standards_included: set = set()

    def add_evidence_packets(
        self,
        model_name: str,
        packets: List[Dict[str, Any]],
        chain_verification: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add evidence packets for a model."""
        self._models_included.add(model_name)
        self._n_evidence_packets += len(packets)

        path = f"evidence/{model_name}/packets.json"
        content = json.dumps(packets, indent=2, sort_keys=True)
        self._files[path] = content.encode("utf-8")

        if chain_verification:
            cv_path = f"evidence/{model_name}/chain_verification.json"
            cv_content = json.dumps(chain_verification, indent=2, sort_keys=True)
            self._files[cv_path] = cv_content.encode("utf-8")

    def add_decision_records(
        self, model_name: str, records: List[Dict[str, Any]]
    ) -> None:
        """Add decision records for a model."""
        self._models_included.add(model_name)
        self._n_decision_records += len(records)

        path = f"decisions/{model_name}/records.json"
        content = json.dumps(records, indent=2, sort_keys=True)
        self._files[path] = content.encode("utf-8")

    def add_compliance_report(
        self, model_name: str, standard: str, report_markdown: str
    ) -> None:
        """Add a compliance report."""
        self._models_included.add(model_name)
        self._standards_included.add(standard)

        path = f"compliance/{model_name}/{standard}_report.md"
        self._files[path] = report_markdown.encode("utf-8")

    def add_merkle_roots(self, roots: List[Dict[str, Any]]) -> None:
        """Add signed daily Merkle roots."""
        self._n_merkle_roots += len(roots)
        for root in roots:
            epoch = root["epoch"]
            path = f"merkle/{epoch}.root.json"
            content = json.dumps(root, indent=2, sort_keys=True)
            self._files[path] = content.encode("utf-8")

    def add_inclusion_proofs(self, proofs: List[Dict[str, Any]]) -> None:
        """Add Merkle inclusion proofs."""
        path = "merkle/inclusion_proofs.json"
        content = json.dumps({"proofs": proofs}, indent=2, sort_keys=True)
        self._files[path] = content.encode("utf-8")

    def _sign_attestation(self, att: Attestation) -> None:
        """Sign an attestation in place using the configured signer."""
        if self.signer is None:
            return
        att.signer = getattr(self.signer, "name", "unknown")
        payload = _canonical_json(att.to_signable_dict()).encode("utf-8")
        att.signature = self.signer.sign_bytes(payload)

    def _build_attestations(self) -> None:
        """Build and sign compliance summary and chain integrity attestations."""
        timestamp = _utc_now()

        # Compliance summary attestation.
        compliance_att = Attestation(
            attestation_type="compliance_summary",
            export_id=self.export_id,
            timestamp=timestamp,
            models={},  # Populated by caller or left as summary.
        )
        compliance_att.content_hash = compliance_att.compute_content_hash()
        self._sign_attestation(compliance_att)

        path = "attestations/compliance_summary.json"
        content = json.dumps(compliance_att.to_dict(), indent=2, sort_keys=True)
        self._files[path] = content.encode("utf-8")

        # Chain integrity attestation.
        chain_att = Attestation(
            attestation_type="chain_integrity",
            export_id=self.export_id,
            timestamp=timestamp,
            models={},
        )
        chain_att.content_hash = chain_att.compute_content_hash()
        self._sign_attestation(chain_att)

        path = "attestations/chain_integrity.json"
        content = json.dumps(chain_att.to_dict(), indent=2, sort_keys=True)
        self._files[path] = content.encode("utf-8")

    def _build_runme(self) -> None:
        """Add RUNME.md."""
        content = (
            "# RiskAttest Evidence Export — Verification\n"
            "\n"
            "```bash\n"
            "python verify_export.py\n"
            "```\n"
            "\n"
            "Requires Python 3.9+. No additional packages needed.\n"
        )
        self._files["RUNME.md"] = content.encode("utf-8")

    def _build_verify_script(self) -> None:
        """Add verify_export.py — zero-dependency verification script."""
        self._files["verify_export.py"] = _VERIFY_SCRIPT.encode("utf-8")

    def build(self, output_path: Path) -> Path:
        """Build the export ZIP archive.

        Parameters
        ----------
        output_path:
            Path for the output ZIP file.

        Returns
        -------
        Path
            The path to the created ZIP file.
        """
        output_path = Path(output_path)

        # Build attestations, RUNME, and verifier.
        self._build_attestations()
        self._build_runme()
        self._build_verify_script()

        # Build manifest (must be last — it hashes all other files).
        manifest = ExportManifest(
            export_id=self.export_id,
            created_at=_utc_now(),
            created_by=self.created_by,
            riskattest_version=self.riskattest_version,
            scope=self.scope.to_dict(),
            files={
                path: {
                    "sha256": _sha256_bytes(content),
                    "size_bytes": len(content),
                }
                for path, content in self._files.items()
            },
            summary={
                "total_evidence_packets": self._n_evidence_packets,
                "total_decision_records": self._n_decision_records,
                "total_merkle_roots": self._n_merkle_roots,
                "models_included": len(self._models_included),
                "standards_included": len(self._standards_included),
            },
        )

        # Write ZIP.
        prefix = f"riskattest-export-{self.export_id}"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Write manifest first.
            manifest_bytes = manifest.to_json().encode("utf-8")
            zf.writestr(f"{prefix}/manifest.json", manifest_bytes)

            # Write all files.
            for path, content in self._files.items():
                zf.writestr(f"{prefix}/{path}", content)

        logger.info("Export written to %s", output_path)
        return output_path


# ---------------------------------------------------------------------------
# verify_export.py — embedded as a string constant
# ---------------------------------------------------------------------------

_VERIFY_SCRIPT = r'''#!/usr/bin/env python3
"""RiskAttest Evidence Export Verification Script.

Zero external dependencies. Requires Python 3.9+.
Run from the export directory:

    python verify_export.py

Exit code 0 = all checks passed.
Exit code 1 = one or more checks failed.
"""

import hashlib
import json
import os
import sys
from pathlib import Path

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def leaf_hash(event_hash_hex: str) -> str:
    return sha256_hex(LEAF_PREFIX + bytes.fromhex(event_hash_hex))


def node_hash(left_hex: str, right_hex: str) -> str:
    return sha256_hex(NODE_PREFIX + bytes.fromhex(left_hex) + bytes.fromhex(right_hex))


class Verifier:
    def __init__(self, export_dir: Path):
        self.export_dir = export_dir
        self.manifest = None
        self.passed = 0
        self.failed = 0

    def run(self) -> bool:
        self._load_manifest()
        if self.manifest is None:
            return False

        self._print_header()
        self._check_manifest_integrity()
        self._check_evidence_chains()
        self._check_merkle_inclusion()
        self._check_attestation_integrity()
        self._print_summary()
        return self.failed == 0

    def _load_manifest(self):
        manifest_path = self.export_dir / "manifest.json"
        if not manifest_path.exists():
            print("ERROR: manifest.json not found")
            self.failed += 1
            return
        with open(manifest_path, "r") as f:
            self.manifest = json.load(f)

    def _print_header(self):
        print()
        print("RiskAttest Evidence Export Verification")
        print("=" * 39)
        scope = self.manifest.get("scope", {})
        dr = scope.get("date_range", {})
        models = scope.get("models", [])
        print(f"Export ID:    {self.manifest['export_id']}")
        print(f"Created:      {self.manifest['created_at']}")
        print(f"Scope:        {', '.join(models)} | {dr.get('start', '?')} to {dr.get('end', '?')}")
        print()

    def _check_manifest_integrity(self):
        files = self.manifest.get("files", {})
        ok = 0
        total = len(files)
        for rel_path, meta in files.items():
            file_path = self.export_dir / rel_path
            if not file_path.exists():
                print(f"  MISSING: {rel_path}")
                self.failed += 1
                continue
            content = file_path.read_bytes()
            actual_hash = sha256_hex(content)
            if actual_hash != meta["sha256"]:
                print(f"  HASH MISMATCH: {rel_path}")
                print(f"    expected: {meta['sha256']}")
                print(f"    actual:   {actual_hash}")
                self.failed += 1
                continue
            ok += 1

        if ok == total and total > 0:
            print(f"[PASS] Manifest integrity     ({ok}/{total} files verified)")
            self.passed += 1
        elif total == 0:
            print("[SKIP] Manifest integrity     (no files)")
        else:
            print(f"[FAIL] Manifest integrity     ({ok}/{total} files verified)")
            self.failed += 1

    def _check_evidence_chains(self):
        evidence_dir = self.export_dir / "evidence"
        if not evidence_dir.exists():
            print("[SKIP] Evidence chain          (no evidence directory)")
            return

        total_packets = 0
        all_valid = True

        for model_dir in sorted(evidence_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            packets_path = model_dir / "packets.json"
            if not packets_path.exists():
                continue

            with open(packets_path, "r") as f:
                packets = json.load(f)

            prev_hash = None
            for pkt in packets:
                total_packets += 1

                # Verify content_hash.
                body = {k: v for k, v in pkt.items() if k not in ("content_hash", "signature")}
                expected_hash = sha256_hex(canonical_json(body).encode("utf-8"))
                if pkt.get("content_hash") != expected_hash:
                    print(f"  HASH INVALID: packet {pkt.get('packet_id', '?')}")
                    all_valid = False
                    continue

                # Verify chain linkage.
                if prev_hash is not None:
                    if pkt.get("prior_packet_hash") != prev_hash:
                        print(f"  CHAIN BREAK: packet {pkt.get('packet_id', '?')}")
                        all_valid = False
                else:
                    if pkt.get("prior_packet_hash") is not None:
                        pass  # May be mid-chain export; acceptable.

                prev_hash = pkt.get("content_hash")

        if all_valid and total_packets > 0:
            print(f"[PASS] Evidence chain          ({total_packets} packets, chain valid)")
            self.passed += 1
        elif total_packets == 0:
            print("[SKIP] Evidence chain          (no packets)")
        else:
            print(f"[FAIL] Evidence chain          ({total_packets} packets, errors found)")
            self.failed += 1

    def _check_merkle_inclusion(self):
        proofs_path = self.export_dir / "merkle" / "inclusion_proofs.json"
        if not proofs_path.exists():
            print("[SKIP] Merkle inclusion        (no inclusion proofs)")
            return

        with open(proofs_path, "r") as f:
            data = json.load(f)

        proofs = data.get("proofs", [])
        ok = 0

        for proof in proofs:
            current = proof["leaf_hash"]
            for step in proof["proof_hashes"]:
                sibling = step["hash"]
                if step["position"] == "left":
                    current = node_hash(sibling, current)
                else:
                    current = node_hash(current, sibling)

            if current == proof["root_hash"]:
                ok += 1
            else:
                print(f"  PROOF INVALID: packet {proof.get('packet_id', '?')}")

        total = len(proofs)
        if ok == total and total > 0:
            print(f"[PASS] Merkle inclusion        ({ok}/{total} proofs verified)")
            self.passed += 1
        elif total == 0:
            print("[SKIP] Merkle inclusion        (no proofs)")
        else:
            print(f"[FAIL] Merkle inclusion        ({ok}/{total} proofs verified)")
            self.failed += 1

    def _check_attestation_integrity(self):
        att_dir = self.export_dir / "attestations"
        if not att_dir.exists():
            print("[SKIP] Attestation integrity   (no attestations)")
            return

        ok = 0
        total = 0

        for att_path in sorted(att_dir.glob("*.json")):
            total += 1
            with open(att_path, "r") as f:
                att = json.load(f)

            # Recompute content_hash.
            body = {k: v for k, v in att.items() if k not in ("content_hash", "signature", "signer")}
            expected = sha256_hex(canonical_json(body).encode("utf-8"))
            if att.get("content_hash") == expected:
                ok += 1
            else:
                print(f"  HASH INVALID: {att_path.name}")

        if ok == total and total > 0:
            print(f"[PASS] Attestation integrity   ({ok}/{total} attestations valid)")
            self.passed += 1
        elif total == 0:
            print("[SKIP] Attestation integrity   (no attestations)")
        else:
            print(f"[FAIL] Attestation integrity   ({ok}/{total} attestations valid)")
            self.failed += 1

    def _print_summary(self):
        print()
        if self.failed == 0:
            print("RESULT: ALL CHECKS PASSED")
        else:
            print(f"RESULT: {self.failed} CHECK(S) FAILED")
        print()


def main():
    # Determine export directory.
    script_dir = Path(__file__).parent
    if (script_dir / "manifest.json").exists():
        export_dir = script_dir
    else:
        # Maybe we're one level up and the export is in a subdirectory.
        candidates = [d for d in script_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
        if candidates:
            export_dir = candidates[0]
        else:
            print("ERROR: Cannot find manifest.json. Run this script from the export directory.")
            sys.exit(1)

    verifier = Verifier(export_dir)
    success = verifier.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
'''
