import asyncio
import tarfile
from pathlib import Path

import pytest

from panoptis.rollback.engine import FilesystemSnapshot


def test_valid_archive_restore(tmp_path: Path):
    """Test that a valid archive restores successfully."""
    # Create target directory
    target_path = tmp_path / "target"
    target_path.mkdir()

    # Create valid archive
    archive_path = tmp_path / "backup.tar.gz"

    # Create some dummy files to archive
    source_path = tmp_path / "source"
    source_path.mkdir()
    (source_path / "config.json").write_text('{"foo": "bar"}')
    (source_path / "state.db").write_text("dummy db content")

    with tarfile.open(archive_path, "w:gz") as tar:
        # Add files with relative paths
        tar.add(source_path / "config.json", arcname="config.json")
        tar.add(source_path / "state.db", arcname="state.db")

    # Restore archive
    restored = asyncio.run(FilesystemSnapshot.restore(archive_path, target_path))

    # Verify files were restored
    assert (target_path / "config.json").exists()
    assert (target_path / "state.db").exists()
    assert len(restored) == 2
    assert "config.json" in restored
    assert "state.db" in restored


def test_malicious_archive_rejected(tmp_path: Path):
    """Test that a malicious archive with path traversal is rejected."""
    # Create target directory
    target_path = tmp_path / "target"
    target_path.mkdir()

    # Create malicious archive manually (tarfile allows writing any arcname)
    archive_path = tmp_path / "evil.tar.gz"

    dummy_file = tmp_path / "dummy.txt"
    dummy_file.write_text("evil content")

    with tarfile.open(archive_path, "w:gz") as tar:
        # Intentionally create path traversal
        tar.add(dummy_file, arcname="../../../../etc/passwd")

    # Restore archive - should be rejected by the data filter
    with pytest.raises(tarfile.FilterError):
        asyncio.run(FilesystemSnapshot.restore(archive_path, target_path))

    # Ensure nothing was extracted to our target path unexpectedly
    assert not (target_path / "etc" / "passwd").exists()
