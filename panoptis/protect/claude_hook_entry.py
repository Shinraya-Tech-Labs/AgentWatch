"""Entry point Claude Code runs as a script file: ``python <this file>``.

It uses only the standard library until the hook module is imported, so a broken or
missing AgentWatch install still blocks the tool call (exit code 2) instead of letting
it through, which is what a failing ``python -m`` import would do (exit code 1).
"""

import sys

# Running a file puts its directory first on sys.path; the package's own modules must
# not shadow anything when imported that way.
if sys.path and sys.path[0].replace("\\", "/").rstrip("/").endswith("panoptis/protect"):
    del sys.path[0]

try:
    from panoptis.protect.claude_hook import main
except BaseException as exc:  # noqa: BLE001 - every failure blocks
    sys.stderr.write(f"AgentWatch hook could not load, blocking the tool call: {exc!r}\n")
    sys.exit(2)

sys.exit(main())
