"""Merkle inclusion proofs (RFC 6962).

Given a set of leaves and a target leaf, produce a proof that the leaf
is included in the Merkle root.  The proof is a list of sibling hashes
with positional hints (``"left"`` or ``"right"``) that let a verifier
walk from the leaf up to the root using only ``hashlib``.

This module is intentionally dependency-free beyond the standard
library and ``mrm.evidence.merkle`` (which is also dependency-free).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

# Re-use the RFC 6962 primitives from the existing Merkle module.
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _leaf_hash(event_hash_hex: str) -> str:
    return _sha256(LEAF_PREFIX + bytes.fromhex(event_hash_hex))


def _node_hash(left_hex: str, right_hex: str) -> str:
    return _sha256(NODE_PREFIX + bytes.fromhex(left_hex) + bytes.fromhex(right_hex))


@dataclass
class InclusionProof:
    """A Merkle inclusion proof binding one leaf to a root."""

    packet_id: str
    epoch: str
    leaf_hash: str
    root_hash: str
    leaf_index: int
    tree_size: int
    proof_hashes: List[Dict[str, str]]  # [{"hash": hex, "position": "left"|"right"}]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InclusionProof":
        return cls(**data)


def build_inclusion_proof(
    leaves: List[str],
    target_index: int,
    packet_id: str,
    epoch: str,
) -> InclusionProof:
    """Build an inclusion proof for the leaf at ``target_index``.

    Parameters
    ----------
    leaves:
        Hex-encoded event hashes in tree order (same order used by
        ``merkle.merkle_root``).
    target_index:
        0-based index of the leaf to prove.
    packet_id:
        The evidence packet ID (carried in the proof for correlation).
    epoch:
        The UTC date ``YYYY-MM-DD`` of the Merkle root.

    Returns
    -------
    InclusionProof
        A proof that can be verified with ``verify_inclusion_proof``.
    """
    if not leaves:
        raise ValueError("Cannot build proof over empty leaf set")
    if target_index < 0 or target_index >= len(leaves):
        raise IndexError(
            f"target_index {target_index} out of range for {len(leaves)} leaves"
        )

    # Compute all leaf hashes.
    level = [_leaf_hash(h) for h in leaves]
    target_leaf = level[target_index]
    proof_hashes: List[Dict[str, str]] = []

    idx = target_index
    while len(level) > 1:
        next_level: List[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(_node_hash(level[i], level[i + 1]))
            else:
                # Promoted unpaired leaf (RFC 6962).
                next_level.append(level[i])

        # Record the sibling if our target has one at this level.
        if idx % 2 == 0:
            # Target is on the left; sibling is on the right (if exists).
            if idx + 1 < len(level):
                proof_hashes.append(
                    {"hash": level[idx + 1], "position": "right"}
                )
        else:
            # Target is on the right; sibling is on the left.
            proof_hashes.append(
                {"hash": level[idx - 1], "position": "left"}
            )

        idx = idx // 2
        level = next_level

    root_hash = level[0]

    return InclusionProof(
        packet_id=packet_id,
        epoch=epoch,
        leaf_hash=target_leaf,
        root_hash=root_hash,
        leaf_index=target_index,
        tree_size=len(leaves),
        proof_hashes=proof_hashes,
    )


def verify_inclusion_proof(proof: InclusionProof) -> bool:
    """Verify a Merkle inclusion proof.

    This function uses only ``hashlib`` — no external dependencies.
    It recomputes the root from the leaf and proof hashes and checks
    it matches the stated ``root_hash``.

    Parameters
    ----------
    proof:
        The inclusion proof to verify.

    Returns
    -------
    bool
        ``True`` iff the proof is valid.
    """
    current = proof.leaf_hash
    for step in proof.proof_hashes:
        sibling = step["hash"]
        if step["position"] == "left":
            current = _node_hash(sibling, current)
        else:
            current = _node_hash(current, sibling)
    return current == proof.root_hash
