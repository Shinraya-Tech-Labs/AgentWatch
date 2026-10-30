"""
Central command execution utility for AgentWatch CLI.
Validates arguments against a strict whitelist to prevent command-injection risks.
"""

from __future__ import annotations

import logging
import re
import subprocess  # nosec B404
from collections.abc import Sequence

logger = logging.getLogger(__name__)

# A comprehensive whitelist of safe characters for arguments:
# - Alphanumerics: a-z, A-Z, 0-9
# - Whitespace: spaces, tabs, newlines
# - Safe path and url symbols: . _ / \ : = + , % @ ~ ? ! * ( ) " ' $ & [ ] { } # ^ -
# Semicolons (;), pipes (|), redirects (<, >), and backticks (`) are explicitly blocked
# to prevent shell command execution if arguments are ever unsafely evaluated.
ALLOWED_ARG_RE = re.compile(r"^[a-zA-Z0-9_./\\:=+,%@~?!\*()\"\'\s$&[\]{}#^-]*$")


class CommandError(Exception):
    """Raised when command validation fails or execution returns a non-zero exit code."""

    def __init__(
        self,
        message: str,
        returncode: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run(
    args: Sequence[str],
    *,
    check_args: bool = True,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute a command securely with shell=False and argument validation.

    Security Model & `check_args`:
    - By default, executing processes with `shell=False` prevents shell injection (such as
      pipes, redirects, or command chaining) because the operating system invokes the target
      executable directly without spawning a command interpreter.
    - When `check_args=True` (default), arguments are additionally validated against a strict character
      whitelist (`ALLOWED_ARG_RE`). This serves as defense-in-depth against:
        1. Argument injection (e.g. passing `-` or `--` option flags controlled by untrusted users).
        2. Downstream shell parsing (where the target process itself interprets arguments inside a shell).

    When is bypassing validation (`check_args=False`) acceptable?
    - Bypassing validation is acceptable ONLY when:
        1. The command contains safe, hardcoded programmatic arguments that use characters blocked
           by the strict whitelist regex (e.g. semicolons, brackets, parentheses, or quotes).
        2. Any user-controlled parts of the arguments have been strictly pre-sanitized or matched
           against a safe allow-list prior to calling this function.
        3. Invoking internal utilities (like git or docker) that require complex syntaxes, provided
           untrusted user input is not concatenated into the arguments.

    Args:
        args: List of command arguments. First element is the executable.
        check_args: If True, validate each argument against ALLOWED_ARG_RE.
        timeout: Optional timeout in seconds.
        env: Optional environment dictionary override.
        cwd: Optional working directory for the subprocess.

    Returns:
        A subprocess.CompletedProcess containing stdout and stderr.

    Raises:
        CommandError: If validation is enabled and an argument contains forbidden characters,
                     or if the command execution fails with a non-zero exit code.
    """
    if not args:
        raise CommandError("Command arguments cannot be empty.")

    if check_args:
        # Validate each argument against the whitelist
        for idx, arg in enumerate(args):
            if not ALLOWED_ARG_RE.match(arg):
                # Find the invalid characters to include in the error message for clarity
                invalid_chars = sorted(list(set(c for c in arg if not ALLOWED_ARG_RE.match(c))))
                raise CommandError(
                    f"Argument at index {idx} contains forbidden characters: "
                    f"'{''.join(invalid_chars)}'. Argument value: {repr(arg)}"
                )

    logger.debug("Executing command: %s", args[0])

    try:
        # Prefer subprocess.run with shell=False
        result = subprocess.run(  # noqa: S603 # nosec B603
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=cwd,
        )
    except Exception as exc:
        logger.error("Failed to execute command %s: %s", args[0], exc)
        raise CommandError(f"Failed to execute command '{args[0]}': {exc}") from exc

    logger.debug("Command exited with code %d", result.returncode)

    if result.returncode != 0:
        logger.error(
            "Command '%s' failed with exit code %d.",
            args[0],
            result.returncode,
        )
        logger.debug(
            "Command '%s' output:\nSTDOUT:\n%s\nSTDERR:\n%s",
            args[0],
            result.stdout,
            result.stderr,
        )
        raise CommandError(
            f"Command failed with exit code {result.returncode}.",
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    return result


def run_validated_command(
    cmd_args: list[str], check: bool = True, timeout: int | float | None = 30.0
) -> tuple[int, str, str]:
    """
    Executes a shell command safely using a list of arguments with shell=False.
    Validates that the command is a non-empty list of strings to prevent injection.
    Uses a bounded timeout by default to avoid hanging subprocesses.
    """
    # Validation Logic: Enforce strict list of strings
    if not cmd_args or not isinstance(cmd_args, list):
        raise ValueError("Command arguments must be provided as a non-empty list.")

    if not all(isinstance(arg, str) for arg in cmd_args):
        raise ValueError("All command arguments must be strings.")

    try:
        # We explicitly use a pre-validated list of strings with shell=False
        result = subprocess.run(  # noqa: S603 # nosec B603
            cmd_args,
            capture_output=True,
            text=True,
            check=check,
            shell=False,
            timeout=timeout,
        )

        return result.returncode, result.stdout.strip(), result.stderr.strip()

    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out after {timeout} seconds: {e}")
        raise
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed with exit code {e.returncode}: {e.stderr}")
        raise
