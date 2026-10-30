"""``python -m panoptis.sensors.native.launch script.py [args...]``

Runs a Python program under the native recorder. Used by ``panoptis observe``.
The program runs in its own process; observations go to ``AGENTWATCH_OBSERVE_FILE``.
The command line is recorded on the run so the run can later be re-executed for replay.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from panoptis import instrument as aw
    from panoptis.sensors.base import FileSink

    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "usage: python -m panoptis.sensors.native.launch script.py [args...]", file=sys.stderr
        )
        return 2
    script = args[0]
    out = os.environ.get("AGENTWATCH_OBSERVE_FILE")
    if not out:
        print("AGENTWATCH_OBSERVE_FILE is not set", file=sys.stderr)
        return 2
    rec = aw.configure(
        FileSink(out), system=os.environ.get("AGENTWATCH_SYSTEM") or Path(script).stem
    )
    replay_spec = os.environ.get("AGENTWATCH_REPLAY_SPEC")
    token = None
    if replay_spec:
        from panoptis.lab.replay_runtime import ReplayController
        from panoptis.sensors.native.recorder import set_replay_controller

        token = set_replay_controller(ReplayController.from_file(replay_spec))
    exit_code = 0
    sys.argv = [script, *args[1:]]
    sys.path.insert(0, str(Path(script).resolve().parent))
    command = json.loads(os.environ.get("AGENTWATCH_COMMAND", "null")) or [sys.executable, *args]
    with rec.run(
        Path(script).stem,
        run_id=os.environ.get("AGENTWATCH_RUN_ID"),
        command=command,
        cwd=os.getcwd(),
        replay_of=os.environ.get("AGENTWATCH_REPLAY_OF"),
        branch_id=os.environ.get("AGENTWATCH_BRANCH_ID"),
    ) as run:
        try:
            runpy.run_path(script, run_name="__main__")
            run.set_outcome("ok")
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
            exit_code = code
            run.set_outcome("ok" if code == 0 else "error")
        except BaseException:
            run.set_outcome("error")
            exit_code = 1
            import traceback

            traceback.print_exc()
        finally:
            if token is not None:
                from panoptis.lab.replay_runtime import finish_controller

                finish_controller()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
