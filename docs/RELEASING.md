# Releasing

There is one release path: `.github/workflows/release.yml`. It replaces the earlier pair of workflows. Both of those created a GitHub Release from any matching tag, the "integrity validation" was an `echo`, and PyPI was published on any `v*` tag without running a single test.

## Steps

1. Bump `version` in `pyproject.toml`.
2. Add a `## [X.Y.Z] - date` section to `CHANGELOG.md`.
3. Merge to `main`.
4. Tag that commit on main and push the tag:

   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```

5. The workflow runs:

   | Job | What | Stops the release when |
   |---|---|---|
   | `ci` | the whole CI (`ci.yml` as a reusable workflow) **on the tagged commit**: lint, types, Bandit, import check, tests with Postgres/Redis, coverage floors, clean install of wheel+sdist with pip-audit, frontend lint/types/tests/audit/build, Docker images, compose smoke test, secret scan | anything fails |
   | `gates` | `scripts/release_check.py tag`: the tag is `vMAJOR.MINOR.PATCH`, equals the `pyproject.toml` version, has a CHANGELOG section; and the tagged commit is an ancestor of `main` | a gate fails |
   | `build` | builds the wheel and sdist **once**, `twine check --strict`, `release_check.py artifacts` (exact file names, required files in the wheel, no unrelated content in the sdist) writes `SHA256SUMS`, then GitHub build-provenance attestation | a check fails |
   | `publish-pypi` | downloads exactly those files, re-verifies `SHA256SUMS` and that only the 2 artifacts are present, publishes | the files changed |
   | `github-release` | the GitHub Release with the same wheel, sdist and `SHA256SUMS`, notes from the CHANGELOG | — |

A manual run (`workflow_dispatch`, `dry_run: true`, a tag) does `ci`, `gates` and `build` and publishes nothing. Use it before tagging a release candidate.

## Settings the repository owner must configure

These are GitHub and PyPI settings, not code.

- **A `pypi` environment** in the repository settings, with required reviewers, so publication waits for an explicit approval.
- **PyPI trusted publishing** (recommended): on PyPI, add a trusted publisher for this repository, the `release.yml` workflow and the `pypi` environment, then delete the `PYPI_TOKEN` secret. Until then the workflow falls back to the `PYPI_TOKEN` secret.
- **Branch protection on `main`**, requiring the CI checks, so the commit the gates accept was reviewed.

## Verifying a published release

```bash
pip download panoptis==X.Y.Z --no-deps -d check/ && cd check
sha256sum -c <(curl -sL https://github.com/Shinraya-Tech-Labs/AgentWatch/releases/download/vX.Y.Z/SHA256SUMS | grep whl)
gh attestation verify panoptis-X.Y.Z-py3-none-any.whl --repo Shinraya-Tech-Labs/AgentWatch
```

## Rollback

PyPI does not allow a version to be re-uploaded.

1. **Yank** the bad version on PyPI (Project → Release history → Options → Yank, with a reason). `pip install panoptis` no longer selects it; exact pins still work.
2. **Mark the GitHub Release** as a pre-release, or delete it, and state why in its notes.
3. **Fix forward:** bump to `X.Y.Z+1`, add the CHANGELOG entry, and release normally.
4. **Never move or re-push a tag:** the attestation and checksums name the original commit.

## Local dry run

```bash
python scripts/release_check.py tag vX.Y.Z
python -m build --outdir dist . && twine check --strict dist/*     # twine >= 7
python scripts/release_check.py artifacts dist vX.Y.Z && python scripts/release_check.py verify-sums dist
sh scripts/clean_install.sh python3.12
```

`tests/test_release_check.py` covers the gates: non-semver tags, version or CHANGELOG mismatch, wrong file names, an incomplete wheel, an sdist with unrelated content, a tampered artifact, and an unlisted extra file.
