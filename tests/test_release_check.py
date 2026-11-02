"""TO-033: the release gates refuse anything that is not exactly the tested, expected build."""

from __future__ import annotations

import importlib.util
import io
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "release_check", ROOT / "scripts" / "release_check.py"
)
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def project(
    tmp_path: Path, version: str = "1.2.3", changelog: str | None = "## [1.2.3] - 2026-11-02\n- x\n"
) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "panoptis"\nversion = "{version}"\n', encoding="utf-8"
    )
    if changelog is not None:
        (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "tag", ["1.2.3", "v1.2", "v1.2.3-rc1", "v1.2.3.4", "vX.Y.Z", "release-1.2.3"]
)
def test_only_semver_tags_release(tmp_path, tag):
    assert rc.check_tag(tag, project(tmp_path)) == [f"tag {tag!r} is not vMAJOR.MINOR.PATCH"]


def test_tag_must_match_pyproject_and_changelog(tmp_path):
    assert rc.check_tag("v1.2.3", project(tmp_path)) == []
    errs = rc.check_tag("v1.2.4", project(tmp_path))
    assert any("pyproject.toml version is 1.2.3" in e for e in errs) and any(
        "CHANGELOG" in e for e in errs
    )
    fresh = tmp_path / "no-changelog"
    fresh.mkdir()
    assert any("CHANGELOG" in e for e in rc.check_tag("v1.2.3", project(fresh, changelog=None)))


def _wheel(dist: Path, name: str, files: list[str]) -> None:
    with zipfile.ZipFile(dist / name, "w") as z:
        for f in files:
            z.writestr(f, "x")


def _sdist(dist: Path, name: str, files: list[str]) -> None:
    with tarfile.open(dist / name, "w:gz") as t:
        for f in files:
            data = b"x"
            info = tarfile.TarInfo(f"{name[:-7]}/{f}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def good_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir(parents=True)
    _wheel(dist, "panoptis-1.2.3-py3-none-any.whl", list(rc.REQUIRED_IN_WHEEL))
    _sdist(
        dist, "panoptis-1.2.3.tar.gz", ["panoptis/__init__.py", "tests/test_x.py", "pyproject.toml"]
    )
    return dist


def test_expected_artifacts_pass_and_get_checksums(tmp_path):
    dist = good_dist(tmp_path)
    assert rc.check_artifacts(dist, "v1.2.3") == []
    assert rc.verify_sums(dist) == []
    assert len((dist / "SHA256SUMS").read_text().splitlines()) == 2


def test_wrong_version_missing_files_and_extra_artifacts_fail(tmp_path):
    dist = good_dist(tmp_path)
    assert any("expected exactly" in e for e in rc.check_artifacts(dist, "v1.2.4"))
    _wheel(dist, "panoptis-1.2.3-py3-none-any.whl", ["panoptis/__init__.py"])  # incomplete wheel
    assert any(
        "lacks panoptis/evidence/verifier.py" in e for e in rc.check_artifacts(dist, "v1.2.3")
    )


def test_sdist_with_unrelated_content_fails(tmp_path):
    dist = good_dist(tmp_path)
    _sdist(
        dist,
        "panoptis-1.2.3.tar.gz",
        ["panoptis/__init__.py", "agentwatch-landing/.next/x.js", "frontend/package.json"],
    )
    errs = rc.check_artifacts(dist, "v1.2.3")
    assert any("agentwatch-landing/" in e and "frontend/" in e for e in errs)


def test_tampered_or_unlisted_artifacts_are_refused(tmp_path):
    dist = good_dist(tmp_path)
    assert rc.check_artifacts(dist, "v1.2.3") == []
    (dist / "panoptis-1.2.3.tar.gz").write_bytes(b"swapped after the checks")
    assert any("does not match" in e for e in rc.verify_sums(dist))
    dist2 = good_dist(tmp_path / "b")
    rc.check_artifacts(dist2, "v1.2.3")
    (dist2 / "evil-0.0.1.tar.gz").write_bytes(b"x")
    assert any("unlisted files" in e for e in rc.verify_sums(dist2))


def test_the_real_project_is_releasable_at_its_own_version():
    import tomllib

    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    errs = rc.check_tag(f"v{version}")
    assert not [e for e in errs if "pyproject" in e]  # the tag/version gate agrees with itself
