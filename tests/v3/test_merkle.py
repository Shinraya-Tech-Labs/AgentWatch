"""Merkle tree with domain separation, inclusion proofs, and an independent verifier."""

from __future__ import annotations

import hashlib
import json

import pytest

from panoptis.evidence.merkle import (
    EMPTY_ROOT,
    MerkleProof,
    MerkleTree,
    hash_leaf,
    hash_node,
    merkle_root,
    verify_inclusion,
)


def leaves(n: int) -> list[str]:
    return [hashlib.sha256(f"witness-{i}".encode()).hexdigest() for i in range(n)]


def external_verify(leaf_hash: str, proof_json: str, root: str) -> bool:
    """A verifier written from the format description alone.

    It imports nothing from panoptis: only ``hashlib`` and ``json``. It reads the proof JSON
    (``leaf_index``, ``tree_size``, ``path``) and rebuilds the root with the RFC 6962 recursion
    (split at the largest power of two below the size), which is a different derivation from the
    iterative one in the library, so agreement between the two is meaningful.
    """
    proof = json.loads(proof_json)
    index, size, path = proof["leaf_index"], proof["tree_size"], proof["path"]
    if not (isinstance(index, int) and isinstance(size, int) and 0 <= index < size):
        return False

    def fold(node: bytes, idx: int, n: int, siblings: list[str]) -> bytes | None:
        if n == 1:
            return node if not siblings else None
        k = 1
        while k * 2 < n:
            k *= 2
        if not siblings:
            return None
        top = bytes.fromhex(siblings[-1])
        if idx < k:
            left = fold(node, idx, k, siblings[:-1])
            return None if left is None else hashlib.sha256(b"\x01" + left + top).digest()
        right = fold(node, idx - k, n - k, siblings[:-1])
        return None if right is None else hashlib.sha256(b"\x01" + top + right).digest()

    start = hashlib.sha256(b"\x00" + bytes.fromhex(leaf_hash)).digest()
    computed = fold(start, index, size, path)
    return computed is not None and computed.hex() == root


# ---- structure ------------------------------------------------------------------------------


def test_known_roots_computed_by_hand() -> None:
    a, b = leaves(2)
    la = hashlib.sha256(b"\x00" + bytes.fromhex(a)).digest()
    lb = hashlib.sha256(b"\x00" + bytes.fromhex(b)).digest()
    assert MerkleTree([a]).root == la.hex()
    assert MerkleTree([a, b]).root == hashlib.sha256(b"\x01" + la + lb).hexdigest()


def test_empty_tree_root_and_no_proofs() -> None:
    tree = MerkleTree([])
    assert tree.root == EMPTY_ROOT == hashlib.sha256(b"").hexdigest()
    assert tree.size == 0
    with pytest.raises(IndexError):
        tree.prove(0)


def test_single_leaf_tree_has_empty_path() -> None:
    (only,) = leaves(1)
    tree = MerkleTree([only])
    proof = tree.prove(0)
    assert proof.path == ()
    assert verify_inclusion(only, proof, tree.root)
    assert tree.root == hash_leaf(only).hex()


def test_root_is_deterministic_and_order_sensitive() -> None:
    ls = leaves(6)
    assert merkle_root(ls) == merkle_root(list(ls))
    assert merkle_root(ls) != merkle_root(list(reversed(ls)))


def test_odd_last_leaf_is_not_duplicated() -> None:
    a, b, c = leaves(3)
    assert merkle_root([a, b, c]) != merkle_root([a, b, c, c])


@pytest.mark.parametrize("n", range(1, 41))
def test_every_leaf_of_every_size_verifies(n: int) -> None:
    ls = leaves(n)
    tree = MerkleTree(ls)
    for i, leaf in enumerate(ls):
        proof = tree.prove(i)
        assert verify_inclusion(leaf, proof, tree.root), (n, i)
        assert external_verify(leaf, json.dumps(proof.to_dict()), tree.root), (n, i)


def test_proof_length_is_logarithmic() -> None:
    tree = MerkleTree(leaves(1000))
    assert max(len(tree.prove(i).path) for i in (0, 1, 499, 998, 999)) <= 10


# ---- independent verifier -------------------------------------------------------------------


def test_independent_verifier_accepts_exported_proof_and_rejects_tamper() -> None:
    ls = leaves(11)
    tree = MerkleTree(ls)
    exported = json.dumps(tree.prove(7).to_dict())
    assert external_verify(ls[7], exported, tree.root)
    assert not external_verify(ls[6], exported, tree.root)
    assert not external_verify(ls[7], exported, leaves(1)[0])


# ---- tamper cases ---------------------------------------------------------------------------


@pytest.fixture
def tree7() -> MerkleTree:
    return MerkleTree(leaves(7))


def test_wrong_leaf_rejected(tree7: MerkleTree) -> None:
    ls = leaves(7)
    assert not verify_inclusion(ls[3], tree7.prove(2), tree7.root)
    assert not verify_inclusion(leaves(8)[7], tree7.prove(2), tree7.root)


def test_wrong_root_rejected(tree7: MerkleTree) -> None:
    ls = leaves(7)
    other = MerkleTree(leaves(8)).root
    assert not verify_inclusion(ls[2], tree7.prove(2), other)
    flipped = ("0" if tree7.root[0] != "0" else "1") + tree7.root[1:]
    assert not verify_inclusion(ls[2], tree7.prove(2), flipped)


def test_wrong_sibling_order_rejected(tree7: MerkleTree) -> None:
    ls = leaves(7)
    proof = tree7.prove(2)
    assert len(proof.path) >= 2
    swapped = MerkleProof(proof.leaf_index, proof.tree_size, tuple(reversed(proof.path)))
    assert not verify_inclusion(ls[2], swapped, tree7.root)


def test_wrong_position_rejected(tree7: MerkleTree) -> None:
    ls = leaves(7)
    proof = tree7.prove(2)
    for bad_index in (0, 1, 3, 4, 6):
        moved = MerkleProof(bad_index, proof.tree_size, proof.path)
        assert not verify_inclusion(ls[2], moved, tree7.root)


def test_wrong_tree_size_rejected_when_it_changes_the_path_shape(tree7: MerkleTree) -> None:
    ls = leaves(7)
    proof = tree7.prove(2)
    for bad_size in (1, 2, 3, 4, 9, 100):
        resized = MerkleProof(proof.leaf_index, bad_size, proof.path)
        assert not verify_inclusion(ls[2], resized, tree7.root)


def test_root_does_not_authenticate_the_exact_tree_size(tree7: MerkleTree) -> None:
    """Known limit, as in RFC 6962: sizes with the same path shape give the same root.

    The leaf position is still pinned (see the wrong position test). The size claim is only as
    trustworthy as whatever authenticates it, which for a token is the signature.
    """
    ls = leaves(7)
    proof = tree7.prove(0)
    assert verify_inclusion(ls[0], MerkleProof(0, 7, proof.path), tree7.root)
    assert verify_inclusion(ls[0], MerkleProof(0, 8, proof.path), tree7.root)


def test_truncated_and_extended_paths_rejected(tree7: MerkleTree) -> None:
    ls = leaves(7)
    proof = tree7.prove(2)
    short = MerkleProof(proof.leaf_index, proof.tree_size, proof.path[:-1])
    long = MerkleProof(proof.leaf_index, proof.tree_size, (*proof.path, proof.path[0]))
    empty = MerkleProof(proof.leaf_index, proof.tree_size, ())
    for bad in (short, long, empty):
        assert not verify_inclusion(ls[2], bad, tree7.root)


def test_tampered_sibling_rejected(tree7: MerkleTree) -> None:
    ls = leaves(7)
    proof = tree7.prove(5)
    path = list(proof.path)
    path[0] = hashlib.sha256(b"forged").hexdigest()
    forged = MerkleProof(proof.leaf_index, proof.tree_size, tuple(path))
    assert not verify_inclusion(ls[5], forged, tree7.root)


def test_interior_node_cannot_pass_as_leaf() -> None:
    """Second-preimage attempt: present an interior node as if it were a leaf."""
    ls = leaves(4)
    tree = MerkleTree(ls)
    left_inner = hash_node(hash_leaf(ls[0]), hash_leaf(ls[1]))
    right_inner = hash_node(hash_leaf(ls[2]), hash_leaf(ls[3]))
    assert hash_node(left_inner, right_inner).hex() == tree.root
    # a 2-leaf "tree" whose leaves are the two interior nodes, with the real root
    forged = MerkleProof(leaf_index=0, tree_size=2, path=(right_inner.hex(),))
    assert not verify_inclusion(left_inner.hex(), forged, tree.root)
    # same with the concatenation presented as one 64-byte "leaf": leaf hashes must be 32 bytes
    assert not verify_inclusion((left_inner + right_inner).hex(), MerkleProof(0, 1, ()), tree.root)


def test_without_prefixes_the_same_attack_would_work() -> None:
    """Why the 0x00 / 0x01 prefixes exist: a plain H(left || right) tree is forgeable this way."""

    def naive_root(ls: list[bytes]) -> bytes:
        level = list(ls)
        while len(level) > 1:
            level = [
                hashlib.sha256(level[i] + level[i + 1]).digest() for i in range(0, len(level), 2)
            ]
        return level[0]

    raw = [hashlib.sha256(bytes([i])).digest() for i in range(4)]
    real = naive_root(raw)
    left_inner = hashlib.sha256(raw[0] + raw[1]).digest()
    right_inner = hashlib.sha256(raw[2] + raw[3]).digest()
    assert naive_root([left_inner, right_inner]) == real  # a different "tree", same root
    # with domain separation the shortcut does not exist
    tree = MerkleTree([r.hex() for r in raw])
    assert merkle_root([left_inner.hex(), right_inner.hex()]) != tree.root


# ---- malformed input ------------------------------------------------------------------------


def test_malformed_inputs_return_false_not_raise(tree7: MerkleTree) -> None:
    ls = leaves(7)
    proof = tree7.prove(2)
    assert not verify_inclusion("not-hex", proof, tree7.root)
    assert not verify_inclusion(ls[2].upper(), proof, tree7.root)
    assert not verify_inclusion(ls[2][:-2], proof, tree7.root)
    assert not verify_inclusion(ls[2], proof, "zz")
    assert not verify_inclusion(ls[2], proof, tree7.root.upper())
    assert not verify_inclusion(ls[2], MerkleProof(2, 7, ("xyz",)), tree7.root)
    assert not verify_inclusion(ls[2], MerkleProof(-1, 7, proof.path), tree7.root)
    assert not verify_inclusion(ls[2], MerkleProof(7, 7, proof.path), tree7.root)
    assert not verify_inclusion(ls[2], MerkleProof(0, 0, ()), tree7.root)
    assert not verify_inclusion(ls[2], MerkleProof("2", 7, proof.path), tree7.root)  # type: ignore[arg-type]


def test_tree_rejects_bad_leaf_hashes() -> None:
    with pytest.raises(ValueError):
        MerkleTree(["abc"])
    with pytest.raises(ValueError):
        MerkleTree([leaves(1)[0].upper()])
    with pytest.raises(ValueError):
        hash_leaf(123)  # type: ignore[arg-type]


def test_prove_index_bounds(tree7: MerkleTree) -> None:
    for bad in (-1, 7, 100):
        with pytest.raises(IndexError):
            tree7.prove(bad)
    with pytest.raises(IndexError):
        tree7.prove(True)  # bool is not an index


def test_proof_json_round_trip_and_validation(tree7: MerkleTree) -> None:
    proof = tree7.prove(4)
    restored = MerkleProof.from_dict(json.loads(json.dumps(proof.to_dict())))
    assert restored == proof
    assert tree7.leaf_hash(4) == leaves(7)[4]
    bad_inputs = [
        {},
        {"leaf_index": 1, "tree_size": 2},
        {"leaf_index": "1", "tree_size": 2, "path": []},
        {"leaf_index": True, "tree_size": 2, "path": []},
        {"leaf_index": 1, "tree_size": 2, "path": "abc"},
        {"leaf_index": 1, "tree_size": 2, "path": ["nothex"]},
        None,
    ]
    for bad in bad_inputs:
        with pytest.raises(ValueError):
            MerkleProof.from_dict(bad)  # type: ignore[arg-type]
