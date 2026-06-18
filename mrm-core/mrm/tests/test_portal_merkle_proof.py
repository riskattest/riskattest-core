"""Tests for Merkle inclusion proofs (regulator-portal-v1 §4)."""

from __future__ import annotations

import hashlib

import pytest

from mrm.portal.merkle_proof import (
    InclusionProof,
    _leaf_hash,
    _node_hash,
    build_inclusion_proof,
    verify_inclusion_proof,
)

# Re-use the same constants as the evidence Merkle module.
LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


# ---------------------------------------------------------------------------
# Primitive hash helpers — sanity checks
# ---------------------------------------------------------------------------


def test_leaf_hash_matches_rfc6962():
    """§4.3: leaf_hash = SHA-256(0x00 || bytes.fromhex(event_hash))."""
    h = "ab" * 32
    expected = hashlib.sha256(LEAF_PREFIX + bytes.fromhex(h)).hexdigest()
    assert _leaf_hash(h) == expected


def test_node_hash_matches_rfc6962():
    """§4.3: node_hash = SHA-256(0x01 || left || right)."""
    left, right = "ab" * 32, "cd" * 32
    expected = hashlib.sha256(
        NODE_PREFIX + bytes.fromhex(left) + bytes.fromhex(right)
    ).hexdigest()
    assert _node_hash(left, right) == expected


# ---------------------------------------------------------------------------
# build_inclusion_proof — basic cases
# ---------------------------------------------------------------------------


def test_proof_single_leaf():
    """A tree with one leaf: proof_hashes is empty."""
    leaves = ["aa" * 32]
    proof = build_inclusion_proof(leaves, 0, "pkt-1", "2026-06-15")
    assert proof.leaf_index == 0
    assert proof.tree_size == 1
    assert proof.proof_hashes == []
    assert proof.leaf_hash == _leaf_hash(leaves[0])
    assert proof.root_hash == _leaf_hash(leaves[0])
    assert verify_inclusion_proof(proof)


def test_proof_two_leaves_left():
    """Prove the left leaf in a two-leaf tree."""
    leaves = ["aa" * 32, "bb" * 32]
    proof = build_inclusion_proof(leaves, 0, "pkt-1", "2026-06-15")
    assert len(proof.proof_hashes) == 1
    assert proof.proof_hashes[0]["position"] == "right"
    assert proof.proof_hashes[0]["hash"] == _leaf_hash(leaves[1])
    assert verify_inclusion_proof(proof)


def test_proof_two_leaves_right():
    """Prove the right leaf in a two-leaf tree."""
    leaves = ["aa" * 32, "bb" * 32]
    proof = build_inclusion_proof(leaves, 1, "pkt-2", "2026-06-15")
    assert len(proof.proof_hashes) == 1
    assert proof.proof_hashes[0]["position"] == "left"
    assert proof.proof_hashes[0]["hash"] == _leaf_hash(leaves[0])
    assert verify_inclusion_proof(proof)


def test_proof_three_leaves_all_positions():
    """Three-leaf tree: unpaired leaf is promoted (RFC 6962)."""
    leaves = ["11" * 32, "22" * 32, "33" * 32]
    for idx in range(3):
        proof = build_inclusion_proof(leaves, idx, f"pkt-{idx}", "2026-06-15")
        assert verify_inclusion_proof(proof), f"Proof failed for index {idx}"


def test_proof_four_leaves_balanced():
    """Balanced four-leaf tree."""
    leaves = ["aa" * 32, "bb" * 32, "cc" * 32, "dd" * 32]
    for idx in range(4):
        proof = build_inclusion_proof(leaves, idx, f"pkt-{idx}", "2026-06-15")
        assert verify_inclusion_proof(proof), f"Proof failed for index {idx}"


def test_proof_five_leaves_unbalanced():
    """Five leaves: unbalanced tree with one promotion."""
    leaves = [f"{i:02x}" * 32 for i in range(5)]
    for idx in range(5):
        proof = build_inclusion_proof(leaves, idx, f"pkt-{idx}", "2026-06-15")
        assert verify_inclusion_proof(proof), f"Proof failed for index {idx}"


def test_proof_large_tree():
    """17-leaf tree — exercises multiple levels of promotion."""
    leaves = [f"{i:02x}" * 32 for i in range(17)]
    for idx in [0, 5, 10, 16]:
        proof = build_inclusion_proof(leaves, idx, f"pkt-{idx}", "2026-06-15")
        assert verify_inclusion_proof(proof), f"Proof failed for index {idx}"


# ---------------------------------------------------------------------------
# Root consistency with existing merkle_root()
# ---------------------------------------------------------------------------


def test_proof_root_matches_merkle_module():
    """The root_hash in the proof must match mrm.evidence.merkle.merkle_root."""
    from mrm.evidence.merkle import merkle_root

    leaves = ["aa" * 32, "bb" * 32, "cc" * 32, "dd" * 32]
    expected_root = merkle_root(leaves)
    proof = build_inclusion_proof(leaves, 0, "pkt-0", "2026-06-15")
    assert proof.root_hash == expected_root


def test_proof_root_matches_merkle_module_odd():
    """Odd number of leaves — root must still match."""
    from mrm.evidence.merkle import merkle_root

    leaves = ["11" * 32, "22" * 32, "33" * 32]
    expected_root = merkle_root(leaves)
    for idx in range(3):
        proof = build_inclusion_proof(leaves, idx, f"pkt-{idx}", "2026-06-15")
        assert proof.root_hash == expected_root


# ---------------------------------------------------------------------------
# Tamper detection
# ---------------------------------------------------------------------------


def test_tampered_leaf_hash_fails():
    """If the leaf_hash is tampered with, verification must fail."""
    leaves = ["aa" * 32, "bb" * 32]
    proof = build_inclusion_proof(leaves, 0, "pkt-0", "2026-06-15")
    proof.leaf_hash = "00" * 32  # Tamper.
    assert not verify_inclusion_proof(proof)


def test_tampered_root_hash_fails():
    """If the root_hash is tampered with, verification must fail."""
    leaves = ["aa" * 32, "bb" * 32]
    proof = build_inclusion_proof(leaves, 0, "pkt-0", "2026-06-15")
    proof.root_hash = "00" * 32  # Tamper.
    assert not verify_inclusion_proof(proof)


def test_tampered_sibling_hash_fails():
    """If a sibling hash in the proof is tampered, verification must fail."""
    leaves = ["aa" * 32, "bb" * 32, "cc" * 32, "dd" * 32]
    proof = build_inclusion_proof(leaves, 0, "pkt-0", "2026-06-15")
    proof.proof_hashes[0]["hash"] = "00" * 32  # Tamper.
    assert not verify_inclusion_proof(proof)


def test_swapped_position_fails():
    """If a sibling's position is swapped, verification must fail."""
    leaves = ["aa" * 32, "bb" * 32]
    proof = build_inclusion_proof(leaves, 0, "pkt-0", "2026-06-15")
    # Swap right -> left.
    proof.proof_hashes[0]["position"] = "left"
    assert not verify_inclusion_proof(proof)


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


def test_inclusion_proof_roundtrip():
    """§4.2: InclusionProof serialises to / from dict."""
    leaves = ["aa" * 32, "bb" * 32, "cc" * 32]
    proof = build_inclusion_proof(leaves, 1, "pkt-1", "2026-06-15")
    d = proof.to_dict()
    restored = InclusionProof.from_dict(d)
    assert restored.packet_id == proof.packet_id
    assert restored.root_hash == proof.root_hash
    assert restored.proof_hashes == proof.proof_hashes
    assert verify_inclusion_proof(restored)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_empty_leaves_raises():
    with pytest.raises(ValueError, match="empty"):
        build_inclusion_proof([], 0, "pkt-0", "2026-06-15")


def test_out_of_range_index_raises():
    with pytest.raises(IndexError):
        build_inclusion_proof(["aa" * 32], 1, "pkt-0", "2026-06-15")


def test_negative_index_raises():
    with pytest.raises(IndexError):
        build_inclusion_proof(["aa" * 32], -1, "pkt-0", "2026-06-15")
