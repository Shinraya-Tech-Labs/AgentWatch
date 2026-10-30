from __future__ import annotations

import builtins
import os
from pathlib import Path

import pytest

from panoptis.lattice.shadow_filesystem import (
    FileAction,
    FileOperation,
    MutationType,
    ShadowFilesystem,
)

ROOT = Path("/srv/workspace")


def _fs(*known: str) -> ShadowFilesystem:
    return ShadowFilesystem(ROOT, known_paths=[Path(p) for p in known])


def _write(path: str) -> FileAction:
    return FileAction(operation=FileOperation.WRITE, path=Path(path))


def _delete(path: str) -> FileAction:
    return FileAction(operation=FileOperation.DELETE, path=Path(path))


# --------------------------------------------------------------------- mutation type


def test_write_to_unknown_path_is_a_create():
    result = _fs().simulate(_write("notes.md"))
    assert result.mutation is MutationType.CREATE
    assert result.existed_before is False


def test_write_to_known_path_is_a_modify():
    result = _fs("/srv/workspace/notes.md").simulate(_write("notes.md"))
    assert result.mutation is MutationType.MODIFY
    assert result.existed_before is True


def test_append_follows_the_same_rule_as_write():
    fs = _fs("/srv/workspace/log.txt")
    append = FileAction(operation=FileOperation.APPEND, path=Path("log.txt"))
    assert fs.simulate(append).mutation is MutationType.MODIFY

    fresh = FileAction(operation=FileOperation.APPEND, path=Path("new.txt"))
    assert fs.simulate(fresh).mutation is MutationType.CREATE


def test_mkdir_on_an_unknown_directory_is_a_create():
    action = FileAction(operation=FileOperation.MKDIR, path=Path("build"))
    assert _fs().simulate(action).mutation is MutationType.CREATE


def test_delete_of_a_known_path_is_a_delete():
    result = _fs("/srv/workspace/stale.log").simulate(_delete("stale.log"))
    assert result.mutation is MutationType.DELETE
    assert result.existed_before is True


def test_delete_of_an_unknown_path_reports_that_nothing_was_there():
    # Still a DELETE — that is what was asked for — but `existed_before` marks it a no-op.
    result = _fs().simulate(_delete("ghost.log"))
    assert result.mutation is MutationType.DELETE
    assert result.existed_before is False


# --------------------------------------------------------------------- path handling


def test_relative_paths_resolve_against_the_root():
    result = _fs().simulate(_write("src/app.py"))
    assert result.target_path == Path("/srv/workspace/src/app.py")


def test_absolute_paths_are_kept_as_given():
    result = _fs().simulate(_write("/data/scratch.txt"))
    assert result.target_path == Path("/data/scratch.txt")


def test_parent_traversal_is_collapsed():
    result = _fs().simulate(_write("src/../config.yml"))
    assert result.target_path == Path("/srv/workspace/config.yml")


def test_traversal_that_climbs_out_of_the_root_is_reported():
    result = _fs().simulate(_write("../../etc/passwd"))
    assert result.target_path == Path("/etc/passwd")
    assert result.escapes_root is True
    assert result.is_critical_path is True


def test_a_path_inside_the_root_does_not_escape():
    assert _fs().simulate(_write("src/app.py")).escapes_root is False


def test_a_sibling_of_the_root_escapes_it():
    # /srv/other shares a parent with the root but is not inside it.
    assert _fs().simulate(_write("/srv/other/file.txt")).escapes_root is True


def test_a_posix_absolute_target_is_not_folded_under_the_root():
    """Regression test: caught by a real Windows CI run, not written speculatively.

    `_normalise` decided whether a path was absolute with `Path.is_absolute()`. That is correct
    on POSIX, but on Windows `PureWindowsPath("/etc/passwd").is_absolute()` is False — there is no
    drive letter — so `root / path` joined it as if it were relative, and `/etc/passwd` silently
    became a path underneath the workspace root. Both `is_critical_path` and `escapes_root` came
    back wrong as a result. This asserts the string form of the target so the test does not
    itself depend on which platform's `Path` class is running.
    """
    result = _fs().simulate(_delete("/etc/passwd"))
    assert str(result.target_path).replace("\\", "/") == "/etc/passwd"
    assert result.is_critical_path is True
    assert result.escapes_root is True


def test_a_relative_root_is_rejected_rather_than_silently_corrupted():
    """Regression test found by CodeRabbit review, verified before fixing.

    The constructor used to call `self._normalise(Path(root), root=Path(root))`, passing the
    (possibly relative) root as both the path and the root argument. For a relative root,
    `_normalise` took the else branch and computed `root / path`, which with the same value on
    both sides doubles it: `ShadowFilesystem(Path("workspace")).root` used to come out as
    `Path("workspace/workspace")` — still relative, not the absolute path the docstring promises.
    Every downstream decision (`_is_critical`, `_escapes_root`, `simulate`) derives from `self.root`,
    so this silently corrupted the safety-relevant state without raising or looking obviously
    wrong locally. Confirmed against the actual pushed code before this fix: it produced exactly
    that doubled path. The constructor now validates and raises rather than continuing silently.
    """
    with pytest.raises(ValueError, match="absolute"):
        ShadowFilesystem(Path("workspace"))


def test_a_root_with_dot_segments_is_still_collapsed():
    # The root itself goes through the same normalisation as any other path, so this is
    # unaffected by the fix above: an absolute root with `.`/`..` segments still collapses.
    fs = ShadowFilesystem(Path("/srv/app/../app"))
    assert fs.root == Path("/srv/app")


# --------------------------------------------------------------------- critical paths


@pytest.mark.parametrize(
    "path",
    ["/etc", "/var", "/boot", "/root", "/home", "/usr", "/bin", "/sbin"],
)
def test_each_critical_directory_is_flagged(path):
    assert _fs().simulate(_delete(path)).is_critical_path is True


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "/usr/local/bin/tool", "/home/alice/.ssh/id_rsa", "/var/log/syslog"],
)
def test_descendants_of_critical_directories_are_flagged(path):
    assert _fs().simulate(_write(path)).is_critical_path is True


@pytest.mark.parametrize("path", ["/srv/workspace/app.py", "/data/scratch", "/opt/tool/bin"])
def test_ordinary_paths_are_not_flagged(path):
    assert _fs().simulate(_write(path)).is_critical_path is False


def test_a_path_merely_prefixed_like_a_critical_one_is_not_flagged():
    # "/etcetera" starts with the characters of "/etc" but is a different directory.
    assert _fs().simulate(_write("/etcetera/notes.md")).is_critical_path is False


# --------------------------------------------------------------------- sequences


def test_apply_makes_a_later_write_read_as_an_overwrite():
    fs = _fs()
    first = fs.apply(_write("report.md"))
    second = fs.simulate(_write("report.md"))

    assert first.mutation is MutationType.CREATE
    assert second.mutation is MutationType.MODIFY


def test_apply_of_a_delete_removes_the_path_from_the_model():
    fs = _fs("/srv/workspace/temp.bin")
    fs.apply(_delete("temp.bin"))

    assert fs.exists(Path("temp.bin")) is False
    assert fs.simulate(_write("temp.bin")).mutation is MutationType.CREATE


def test_simulate_leaves_the_model_untouched():
    fs = _fs()
    before = set(fs.known_paths)
    fs.simulate(_write("a.txt"))
    fs.simulate(_delete("a.txt"))
    assert fs.known_paths == before


# --------------------------------------------------------------------- the I/O ban


def test_simulation_works_for_a_root_that_does_not_exist_on_disk():
    # Nothing under this root is real, so any implementation that consulted the disk would either
    # fail or give the wrong answer here.
    fs = ShadowFilesystem(
        Path("/nonexistent/definitely/not/here"),
        known_paths=[Path("/nonexistent/definitely/not/here/a.txt")],
    )
    assert fs.simulate(_write("a.txt")).mutation is MutationType.MODIFY
    assert fs.simulate(_write("b.txt")).mutation is MutationType.CREATE


def test_no_filesystem_call_is_made_during_simulation(monkeypatch):
    """The crucial constraint: simulation must never touch the disk.

    Every filesystem entry point the class could plausibly reach is wrapped in a recorder. The
    wrappers still call through, so pytest's own machinery keeps working; the assertion is simply
    that none of them fired while the simulator was running.
    """
    calls: list[str] = []

    def _record(name, original):
        def wrapper(*args, **kwargs):
            calls.append(name)
            return original(*args, **kwargs)

        return wrapper

    for attr in ("resolve", "exists", "stat", "is_file", "is_dir", "iterdir", "glob", "open"):
        monkeypatch.setattr(Path, attr, _record(f"Path.{attr}", getattr(Path, attr)))
    for name in ("stat", "lstat", "listdir", "scandir", "walk"):
        monkeypatch.setattr(os, name, _record(f"os.{name}", getattr(os, name)))
    monkeypatch.setattr(builtins, "open", _record("open", builtins.open))

    fs = ShadowFilesystem(ROOT, known_paths=[Path("/srv/workspace/existing.txt")])

    # Anything the recorders caught before this point belongs to test setup, not the simulator.
    calls.clear()

    for action in (
        _write("existing.txt"),
        _write("new.txt"),
        _delete("existing.txt"),
        _write("../../etc/passwd"),
        FileAction(operation=FileOperation.MKDIR, path=Path("build/out")),
    ):
        result = fs.simulate(action)
        # `Path.is_absolute()` is platform-dependent for a POSIX-style path with no drive
        # letter: PureWindowsPath("/etc/passwd").is_absolute() is False. This simulator's
        # targets are POSIX paths regardless of what OS the test happens to run on, so check
        # the string form instead of relying on the platform `Path` class's notion of absolute.
        assert str(result.target_path).replace("\\", "/").startswith("/")

    fs.apply(_write("another.txt"))
    fs.exists(Path("another.txt"))

    assert calls == [], f"ShadowFilesystem performed real filesystem I/O: {sorted(set(calls))}"
