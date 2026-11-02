"""Release gates that run before anything is published (TO-033).

    python scripts/release_check.py tag v0.3.1                # tag format, pyproject version, changelog
    python scripts/release_check.py artifacts dist/ v0.3.1    # wheel/sdist names, contents, SHA256SUMS
    python scripts/release_check.py verify-sums dist/         # the files still match SHA256SUMS

Each command exits 1 with the reasons when a gate fails. The release workflow calls them;
tests/test_release_check.py covers the gates.
"""

from __future__ import annotations

import hashlib
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
REQUIRED_IN_WHEEL = (
    "panoptis/__init__.py",
    "panopti/__init__.py",
    "panoptis/protect/enforce.py",
    "panoptis/protect/claude_hook_entry.py",
    "panoptis/evidence/verifier.py",
)
FORBIDDEN_IN_SDIST = (
    "agentwatch-landing/",
    "frontend/",
    "node_modules/",
    ".next/",
    ".env",
    "vault/",
)


def check_tag(tag: str, root: Path = ROOT) -> list[str]:
    errors = []
    m = TAG.match(tag)
    if not m:
        return [f"tag {tag!r} is not vMAJOR.MINOR.PATCH"]
    version = tag[1:]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if project["version"] != version:
        errors.append(f"pyproject.toml version is {project['version']}, the tag is {tag}")
    changelog = root / "CHANGELOG.md"
    if not changelog.is_file() or not re.search(
        rf"^##\s.*\b{re.escape(version)}\b", changelog.read_text(encoding="utf-8"), re.M
    ):
        errors.append(f"CHANGELOG.md has no '## ... {version}' section")
    return errors


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_artifacts(dist: Path, tag: str) -> list[str]:
    version = tag[1:]
    errors = []
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if [w.name for w in wheels] != [f"panoptis-{version}-py3-none-any.whl"]:
        errors.append(
            f"expected exactly panoptis-{version}-py3-none-any.whl, found {[w.name for w in wheels]}"
        )
    if [s.name for s in sdists] != [f"panoptis-{version}.tar.gz"]:
        errors.append(
            f"expected exactly panoptis-{version}.tar.gz, found {[s.name for s in sdists]}"
        )
    for w in wheels:
        names = set(zipfile.ZipFile(w).namelist())
        for req in REQUIRED_IN_WHEEL:
            if req not in names:
                errors.append(f"{w.name} lacks {req}")
        if any(n.startswith("tests/") or "__pycache__" in n for n in names):
            errors.append(f"{w.name} contains tests or caches")
    for s in sdists:
        with tarfile.open(s) as t:
            names = t.getnames()
        bad = sorted(
            {f for n in names for f in FORBIDDEN_IN_SDIST if f"/{f}" in f"/{n.split('/', 1)[-1]}"}
        )
        if bad:
            errors.append(f"{s.name} contains {bad}")
    if not errors:
        sums = "".join(f"{_sha256(p)}  {p.name}\n" for p in [*wheels, *sdists])
        (dist / "SHA256SUMS").write_text(sums, encoding="utf-8")
    return errors


def verify_sums(dist: Path) -> list[str]:
    sums = dist / "SHA256SUMS"
    if not sums.is_file():
        return ["SHA256SUMS missing"]
    errors = []
    listed = set()
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, _, name = line.partition("  ")
        listed.add(name)
        f = dist / name
        if not f.is_file():
            errors.append(f"{name} listed but missing")
        elif _sha256(f) != digest:
            errors.append(f"{name} does not match its SHA256SUMS entry")
    extra = sorted(
        p.name for p in dist.iterdir() if p.name != "SHA256SUMS" and p.name not in listed
    )
    if extra:
        errors.append(f"unlisted files would be published: {extra}")
    return errors


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "tag":
        errors = check_tag(argv[1])
    elif len(argv) >= 3 and argv[0] == "artifacts":
        errors = check_artifacts(Path(argv[1]), argv[2])
    elif len(argv) >= 2 and argv[0] == "verify-sums":
        errors = verify_sums(Path(argv[1]))
    else:
        print(__doc__)
        return 2
    for e in errors:
        print(f"release gate failed: {e}")
    if not errors:
        print("release gate passed")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
