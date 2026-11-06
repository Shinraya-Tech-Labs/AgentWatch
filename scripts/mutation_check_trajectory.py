#!/usr/bin/env python3
"""Mutation check for the trajectory limits and eviction rules.

Each mutant below breaks one limit or one eviction rule on purpose, in a temporary copy of the
code, and the trajectory tests must then fail. A surviving mutant means a guarantee could break
with nobody noticing, so this script exits non-zero unless every mutant is caught::

    python scripts/mutation_check_trajectory.py            # all mutants
    python scripts/mutation_check_trajectory.py T05 T07    # named mutants

``tests/protect/test_trajectory_mutation_definitions.py`` keeps these definitions in step with
the code, so a mutant cannot quietly stop applying.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
LTL = "panoptis/protect/ltl_trajectory.py"
PIPELINE = "panoptis/protect/pipeline.py"
TESTS = (
    "tests/protect/test_trajectory_capacity.py",
    "tests/protect/test_final_remediation.py",
    "tests/protect/test_trajectory_and_stego_reliability.py",
)


class Mutant(NamedTuple):
    name: str
    path: str
    old: str
    new: str


FACTS_FULL = "if len(tenant.facts) >= self.max_facts_per_tenant:"
PATHS_FULL = "or tenant.tracked_files >= self.max_tracked_files"
VERIFY_TOUCH = (
    "        if session in tenant.facts:\n"
    "            tenant.facts.move_to_end(session)\n"
    "        findings: list[Finding] = []\n"
)
RECORD_TOUCH = (
    "        if session in tenant.facts:\n"
    "            tenant.facts.move_to_end(session)\n"
    "\n"
    "        if reads_sens or untrusted:\n"
    "            facts = self._open_facts"
)
SEAL_LOG = "        count = tenant.sealed.count\n        if count & (count - 1) == 0:"
OPEN_PREFIX_LOOP = "for size in range(min(_MAX_PREFIX_CHARS, len(text)), 0, -1):"
REMOVE_FILE = "                del files[key]\n                tenant.tracked_files -= 1\n"

MUTANTS = (
    # session exhaustion
    Mutant("T01 table never fills", LTL, FACTS_FULL, "if False:"),
    Mutant("T02 eviction forgets instead of sealing", LTL, "tenant.sealed.add(session)", "pass"),
    Mutant(
        "T03 newest sealed, not oldest",
        LTL,
        "tenant.facts.popitem(last=False)",
        "tenant.facts.popitem(last=True)",
    ),
    Mutant(
        "T04 sealing leaks path budget",
        LTL,
        "tenant.tracked_files -= len(facts.files)\n        if tenant.sealed is None:",
        "if tenant.sealed is None:",
    ),
    Mutant("T05 no tenant path cap", LTL, PATHS_FULL, "or False"),
    Mutant("T06 no session path cap", LTL, "len(files) >= self.max_files_per_session", "False"),
    Mutant(
        "T07 overflow forgets earlier writes",
        LTL,
        "key, append = _OVERFLOW_PATH, True",
        "key, append = _OVERFLOW_PATH, False",
    ),
    Mutant("T08 overflow finding removed", LTL, "if facts.overflowed and executes:", "if False:"),
    Mutant(
        "T09 sealed session judged on nothing",
        LTL,
        "        facts = _WORST_CASE if sealed else tenant.facts.get(session, _NO_FACTS)\n"
        "        if session in tenant.facts:\n",
        "        facts = tenant.facts.get(session, _NO_FACTS)\n"
        "        if session in tenant.facts:\n",
    ),
    Mutant("T10 sealing not explained", LTL, "if sealed and findings:", "if False:"),
    Mutant(
        "T11 sealed session tracked again",
        LTL,
        "        if tenant.is_sealed(session):\n            return  # judged",
        "        if False:\n            return  # judged",
    ),
    Mutant(
        "T12 verify is not activity", LTL, VERIFY_TOUCH, "        findings: list[Finding] = []\n"
    ),
    Mutant(
        "T13 record is not activity",
        LTL,
        RECORD_TOUCH,
        "        if reads_sens or untrusted:\n            facts = self._open_facts",
    ),
    Mutant(
        "T14 worst case omits sensitive read",
        LTL,
        "sensitive_read=_UNKNOWN,",
        "sensitive_read=None,",
    ),
    Mutant(
        "T15 worst case omits untrusted input",
        LTL,
        "untrusted_ingest=_UNKNOWN,",
        "untrusted_ingest=None,",
    ),
    Mutant(
        "T16 worst case omits indicators",
        LTL,
        "indicators=frozenset(_INDICATORS)",
        "indicators=frozenset()",
    ),
    Mutant(
        "T17 sealing is silent",
        LTL,
        SEAL_LOG,
        "        count = tenant.sealed.count\n        if False:",
    ),
    Mutant(
        "T18 every seal is logged",
        LTL,
        SEAL_LOG,
        "        count = tenant.sealed.count\n        if True:",
    ),
    Mutant(
        "T19 filter matches on any bit",
        LTL,
        "return all(self._bits[p >> 3]",
        "return any(self._bits[p >> 3]",
    ),
    Mutant(
        "T20 predictable filter key",
        LTL,
        "self._seal_key = secrets.token_bytes(16)",
        'self._seal_key = b"0" * 16',
    ),
    Mutant(
        "T21 filter overrides an exact record",
        LTL,
        "return session not in self.facts and self.sealed is not None and session in self.sealed",
        "return self.sealed is not None and session in self.sealed",
    ),
    # tenant isolation
    Mutant(
        "I01 fact table shared by tenants",
        LTL,
        FACTS_FULL,
        "if sum(len(t.facts) for t in self._tenants.values()) >= self.max_facts_per_tenant:",
    ),
    Mutant(
        "I02 path budget shared by tenants",
        LTL,
        PATHS_FULL,
        "or sum(t.tracked_files for t in self._tenants.values()) >= self.max_tracked_files",
    ),
    Mutant(
        "I03 full tenant table lets the step through",
        LTL,
        "            if tenant is None:\n                return [\n",
        "            if tenant is None:\n                return []\n                return [\n",
    ),
    Mutant(
        "I04 tenant table has no limit",
        LTL,
        "if len(self._tenants) >= self.max_tenants:",
        "if False:",
    ),
    Mutant(
        "I05 place not reserved before the step",
        LTL,
        "tenant = self._admit(tenant_id)  # reserved now",
        "tenant = _Tenant() if len(self._tenants) < self.max_tenants else None  # reserved now",
    ),
    Mutant(
        "I06 every tenant takes a place",
        LTL,
        "            if not self._creates_state(action):\n"
        "                return []  # nothing is known",
        "            if False:\n                return []  # nothing is known",
    ),
    Mutant(
        "I07 history pressure ignores who uses most",
        LTL,
        "tenant_id = max(self._history, key=lambda t: size(self._history[t]))",
        "tenant_id = next(iter(self._history))",
    ),
    Mutant(
        "I08 no history session limit",
        LTL,
        "while self._history_sessions >= self.max_sessions:",
        "while False:",
    ),
    Mutant(
        "I09 no history step limit",
        LTL,
        "while self._history_steps > self.max_history_steps:",
        "while False:",
    ),
    Mutant(
        "I10 releasing a tenant leaks its session count",
        LTL,
        "                self._history_sessions -= len(history.sessions)\n",
        "",
    ),
    Mutant(
        "I11 releasing a tenant leaks its step count",
        LTL,
        "                self._history_steps -= history.steps\n",
        "",
    ),
    Mutant(
        "I12 eviction takes from the wrong measure",
        LTL,
        "self._evict_history(lambda h: h.steps)",
        "self._evict_history(lambda h: len(h.sessions))",
    ),
    Mutant(
        "I13 a sensitive read needs no place",
        LTL,
        "        if reads_sens or untrusted:\n            return True\n",
        "        if untrusted:\n            return True\n",
    ),
    Mutant(
        "I14 untrusted input needs no place",
        LTL,
        "        if reads_sens or untrusted:\n            return True\n",
        "        if reads_sens:\n            return True\n",
    ),
    Mutant(
        "I15 a relevant write needs no place",
        LTL,
        "            if assembly.holds_anything():\n                return True\n",
        "            if False:\n                return True\n",
    ),
    Mutant(
        "I16 refusals are silent",
        LTL,
        "if self._refused_tenant_calls & (self._refused_tenant_calls - 1) == 0:",
        "if False:",
    ),
    Mutant(
        "I17 every refusal is logged",
        LTL,
        "if self._refused_tenant_calls & (self._refused_tenant_calls - 1) == 0:",
        "if True:",
    ),
    # capacity recovery
    Mutant(
        "R01 overwrite keeps the session",
        LTL,
        "                    del tenant.facts[session]\n",
        "                    pass\n",
    ),
    Mutant(
        "R02 overwrite leaks path budget",
        LTL,
        REMOVE_FILE,
        "                del files[key]\n",
    ),
    Mutant(
        "R03 overwrite keeps the file",
        LTL,
        REMOVE_FILE,
        "                tenant.tracked_files -= 0\n",
    ),
    Mutant(
        "R04 release leaks path budget",
        LTL,
        "            if tenant is not None and facts is not None:\n"
        "                tenant.tracked_files -= len(facts.files)\n",
        "",
    ),
    Mutant(
        "R05 forgetting history leaks its steps",
        LTL,
        "        history.steps -= len(trace)\n        self._history_steps -= len(trace)\n",
        "",
    ),
    Mutant(
        "R06 release keeps the facts",
        LTL,
        "facts = tenant.facts.pop(session_id, None) if tenant is not None else None",
        "facts = tenant.facts.get(session_id) if tenant is not None else None",
    ),
    Mutant(
        "R07 release tenant keeps the state",
        LTL,
        "state = self._tenants.pop(tenant_id, None)",
        "state = self._tenants.get(tenant_id)",
    ),
    Mutant(
        "R08 emptied tenant history is kept",
        LTL,
        "        if not history.sessions:\n            del self._history[tenant_id]\n",
        "",
    ),
    # relevance and multi-step facts
    Mutant(
        "F01 nothing relevant still costs state",
        LTL,
        "if not assembly.holds_anything():",
        "if False:",
    ),
    Mutant("F02 open prefix dropped", LTL, "self.tail = _open_prefix(text)", 'self.tail = ""'),
    Mutant(
        "F03 open prefix too short",
        LTL,
        OPEN_PREFIX_LOOP,
        "for size in range(min(_MAX_PREFIX_CHARS - 1, len(text)), 0, -1):",
    ),
    Mutant(
        "F04 shortest open prefix kept",
        LTL,
        OPEN_PREFIX_LOOP,
        "for size in range(1, min(_MAX_PREFIX_CHARS, len(text)) + 1):",
    ),
    Mutant(
        "F05 past the cap only relevant writes merge",
        LTL,
        "if facts is not None and facts.overflowed and key not in files:",
        "if False:",
    ),
    Mutant(
        "F06 sensitive read not recorded",
        LTL,
        "if reads_sens and facts.sensitive_read is None:",
        "if False:",
    ),
    Mutant(
        "F07 untrusted input not recorded",
        LTL,
        "if untrusted and facts.untrusted_ingest is None:",
        "if False:",
    ),
    Mutant(
        "F08 later read replaces the first",
        LTL,
        "if reads_sens and facts.sensitive_read is None:",
        "if reads_sens:",
    ),
    Mutant(
        "F09 later ingest replaces the first",
        LTL,
        "if untrusted and facts.untrusted_ingest is None:",
        "if untrusted:",
    ),
    Mutant(
        "F10 context ignores sealing",
        LTL,
        "facts = _WORST_CASE if sealed else tenant.facts.get(session, _NO_FACTS)\n            return {",
        "facts = tenant.facts.get(session, _NO_FACTS)\n            return {",
    ),
    Mutant(
        "F11 simulator gets no facts",
        PIPELINE,
        "self.ltl.session_context(action)\n                    if self.ltl is not None",
        "None\n                    if False",
    ),
)


def _copy_tree(target: Path) -> None:
    for name in ("panoptis", "panopti"):
        shutil.copytree(ROOT / name, target / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", target / "pyproject.toml")
    (target / "tests" / "protect").mkdir(parents=True)
    shutil.copy(ROOT / "tests" / "conftest.py", target / "tests" / "conftest.py")
    for path in TESTS:
        shutil.copy(ROOT / path, target / path)


def _run(target: Path, tag: str) -> tuple[int, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("DATABASE_URL", "REDIS_URL", "AGENTWATCH_TEST_PG_URL")
    }
    env |= {"PYTHONPATH": str(target), "PYTHONDONTWRITEBYTECODE": "1"}
    run = subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            "--no-header",
            f"--basetemp={target.parent / f'pt-{tag}'}",
            *TESTS,
        ],
        cwd=target,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    lines = run.stdout.strip().splitlines() or [""]
    failed = next((x for x in lines if x.startswith("FAILED")), lines[-1])
    return run.returncode, failed[:110]


def main(argv: list[str]) -> int:
    wanted = set(argv)
    chosen = [m for m in MUTANTS if not wanted or m.name.split()[0] in wanted]
    survivors = []
    with tempfile.TemporaryDirectory(prefix="aw-mutation-") as tmp:
        target = Path(tmp) / "tree"
        target.mkdir()
        _copy_tree(target)
        code, summary = _run(target, "base")
        print("baseline:", summary, flush=True)
        if code != 0:
            print("the unmodified code must pass first")
            return 2
        for number, mutant in enumerate(chosen):
            path = target / mutant.path
            original = path.read_text(encoding="utf-8")
            if mutant.old not in original:
                print(f"NOT APPLIED  {mutant.name}")
                survivors.append(mutant.name)
                continue
            path.write_text(original.replace(mutant.old, mutant.new), encoding="utf-8")
            started = time.time()
            try:
                code, summary = _run(target, str(number))
            finally:
                path.write_text(original, encoding="utf-8")
            state = "killed  " if code == 1 else "SURVIVED"
            print(f"{state} {mutant.name} ({time.time() - started:.0f}s) {summary}", flush=True)
            if code != 1:
                survivors.append(mutant.name)
    print(f"caught {len(chosen) - len(survivors)} of {len(chosen)}")
    for name in survivors:
        print("  survived:", name)
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
