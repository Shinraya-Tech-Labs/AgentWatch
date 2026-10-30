"""Content-addressed, write-once blob storage for large payloads and artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Protocol


class BlobStore(Protocol):
    def put(self, data: bytes) -> str: ...
    def get(self, digest: str) -> bytes: ...
    def exists(self, digest: str) -> bool: ...
    def delete(self, digest: str) -> bool: ...


class BlobIntegrityError(RuntimeError):
    pass


class FsBlobStore:
    """Blobs at ``<root>/<aa>/<bb>/<sha256>``. Writes are atomic and never overwrite."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"invalid blob digest {digest!r}")
        return self.root / digest[:2] / digest[2:4] / digest

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(digest)
        if path.exists():
            return digest
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            if path.exists():  # another writer won the race; content is identical
                os.unlink(tmp)
            else:
                os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return digest

    def get(self, digest: str) -> bytes:
        data = self._path(digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise BlobIntegrityError(f"blob {digest} failed integrity check")
        return data

    def exists(self, digest: str) -> bool:
        return self._path(digest).exists()

    def delete(self, digest: str) -> bool:
        """Remove a blob (retention/erasure). Returns whether it existed."""
        try:
            self._path(digest).unlink()
        except FileNotFoundError:
            return False
        return True


class MemoryBlobStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        self._data.setdefault(digest, bytes(data))
        return digest

    def get(self, digest: str) -> bytes:
        return self._data[digest]

    def exists(self, digest: str) -> bool:
        return digest in self._data

    def delete(self, digest: str) -> bool:
        return self._data.pop(digest, None) is not None
