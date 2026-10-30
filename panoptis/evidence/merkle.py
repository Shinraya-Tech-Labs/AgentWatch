"""Domain-separated Merkle tree over witness leaf hashes, with inclusion proofs.

Standard library only (``hashlib``). The construction follows the Merkle tree of RFC 6962
(section 2.1) and the audit path verification of RFC 9162 (section 2.1.3.2):

* leaf node:     ``SHA-256(0x00 || leaf_hash)``
* interior node: ``SHA-256(0x01 || left || right)``
* empty tree:    ``SHA-256("")``

``leaf_hash`` is the 32-byte digest of one witness (for example the SHA-256 of an encrypted
payload, see :mod:`panoptis.evidence.shredding`). The two prefixes keep leaf and interior
hashes in separate domains, so an interior node can never be presented as a leaf (the classic
second-preimage trick on a Merkle tree).

An odd node at the end of a level is promoted to the next level unchanged. It is not paired with
a copy of itself, so two different leaf lists can never produce the same root by repeating the
last leaf.

A proof is ``(leaf_index, tree_size, path)``. A verifier needs only the root, the proof and the
leaf hash. It never sees the tree. The left or right position of each sibling is derived from
``leaf_index`` and ``tree_size`` while walking the path, so a proof cannot be re-ordered or
re-positioned without changing the computed root. The root does not authenticate the exact
``tree_size`` (as in RFC 6962, sizes with the same path shape give the same root); take the size
from a signed source such as an audit token.

What this does not do: it does not prove *when* a segment was sealed, and it does not prove a
segment is complete. See ``docs/v3/WITNESS_ATTESTATION.md``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
EMPTY_ROOT = hashlib.sha256(b"").hexdigest()

_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


def _digest_bytes(value: Any, name: str) -> bytes:
    """Decode a lowercase 64-character hex SHA-256 digest, or raise ``ValueError``."""
    if not isinstance(value, str) or _HEX_DIGEST.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase hex SHA-256 digest (64 characters)")
    return bytes.fromhex(value)


def hash_leaf(leaf_hash: str) -> bytes:
    """Tree-leaf hash: ``SHA-256(0x00 || leaf_hash)``."""
    return hashlib.sha256(LEAF_PREFIX + _digest_bytes(leaf_hash, "leaf_hash")).digest()


def hash_node(left: bytes, right: bytes) -> bytes:
    """Interior hash: ``SHA-256(0x01 || left || right)``."""
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


@dataclass(frozen=True, slots=True)
class MerkleProof:
    """Inclusion proof for one leaf of a sealed segment.

    ``path`` lists sibling hashes (lowercase hex) from the leaf level upwards. Levels where the
    node had no sibling (an odd node that was promoted) contribute no entry.
    """

    leaf_index: int
    tree_size: int
    path: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "leaf_index": self.leaf_index,
            "tree_size": self.tree_size,
            "path": list(self.path),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MerkleProof:
        """Parse the JSON form. Raises ``ValueError`` on anything malformed."""
        try:
            leaf_index = data["leaf_index"]
            tree_size = data["tree_size"]
            path = data["path"]
        except (KeyError, TypeError) as exc:
            raise ValueError("proof must have leaf_index, tree_size and path") from exc
        if (
            not isinstance(leaf_index, int)
            or isinstance(leaf_index, bool)
            or not isinstance(tree_size, int)
            or isinstance(tree_size, bool)
        ):
            raise ValueError("leaf_index and tree_size must be integers")
        if not isinstance(path, list | tuple):
            raise ValueError("path must be a list of hex digests")
        for item in path:
            _digest_bytes(item, "path element")
        return cls(leaf_index=leaf_index, tree_size=tree_size, path=tuple(path))


class MerkleTree:
    """A sealed Merkle tree over an ordered sequence of witness leaf hashes."""

    def __init__(self, leaf_hashes: Sequence[str]) -> None:
        self._leaf_hashes: tuple[str, ...] = tuple(leaf_hashes)
        level = [hash_leaf(h) for h in self._leaf_hashes]
        self._levels: list[list[bytes]] = [level]
        while len(level) > 1:
            nxt = [hash_node(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
            if len(level) % 2:
                nxt.append(level[-1])
            self._levels.append(nxt)
            level = nxt

    @property
    def size(self) -> int:
        return len(self._leaf_hashes)

    @property
    def root(self) -> str:
        """Root hash as lowercase hex."""
        if not self._leaf_hashes:
            return EMPTY_ROOT
        return self._levels[-1][0].hex()

    def leaf_hash(self, index: int) -> str:
        return self._leaf_hashes[index]

    def prove(self, index: int) -> MerkleProof:
        """Inclusion proof for the leaf at ``index``."""
        if isinstance(index, bool) or not 0 <= index < self.size:
            raise IndexError(f"leaf index {index} out of range for tree of size {self.size}")
        path: list[str] = []
        idx = index
        for level in self._levels[:-1]:
            sibling = idx ^ 1
            if sibling < len(level):
                path.append(level[sibling].hex())
            idx //= 2
        return MerkleProof(leaf_index=index, tree_size=self.size, path=tuple(path))


def merkle_root(leaf_hashes: Sequence[str]) -> str:
    """Root of the tree over ``leaf_hashes`` (convenience for ``MerkleTree(...).root``)."""
    return MerkleTree(leaf_hashes).root


def _root_from_proof(leaf_hash: str, proof: MerkleProof) -> bytes | None:
    """Fold a proof into a root (RFC 9162 section 2.1.3.2). ``None`` if structurally invalid."""
    if proof.tree_size < 1 or not 0 <= proof.leaf_index < proof.tree_size:
        return None
    node = hash_leaf(leaf_hash)
    fn = proof.leaf_index
    sn = proof.tree_size - 1
    for sibling_hex in proof.path:
        if sn == 0:
            return None  # more path elements than the tree has levels
        sibling = _digest_bytes(sibling_hex, "path element")
        if fn & 1 or fn == sn:
            node = hash_node(sibling, node)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            node = hash_node(node, sibling)
        fn >>= 1
        sn >>= 1
    return node if sn == 0 else None


def verify_inclusion(leaf_hash: str, proof: MerkleProof, root: str) -> bool:
    """True when ``proof`` shows ``leaf_hash`` is in the tree with root ``root``.

    Needs only the leaf hash, the proof and the root. Malformed input returns ``False``
    instead of raising, so a verifier can treat any failure uniformly.
    """
    try:
        expected = _digest_bytes(root, "root")
        computed = _root_from_proof(leaf_hash, proof)
    except (ValueError, TypeError):
        return False
    return computed is not None and computed == expected
