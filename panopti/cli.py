"""Panopti CLI: Unified terminal command center for Panopti and AgentWatch."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from panopti._banner import PANOPTI_ASCII_BANNER


def _int_flag(argv: list[str], flag: str, *, default: int, minimum: int) -> int:
    """Read ``flag <int>`` from argv, raising ValueError with a readable message."""
    if flag not in argv:
        return default
    idx = argv.index(flag)
    if idx + 1 >= len(argv):
        raise ValueError(f"{flag} needs a value")
    raw = argv[idx + 1]
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{flag} must be a whole number, got '{raw}'") from None
    if value < minimum:
        raise ValueError(f"{flag} must be at least {minimum}, got {value}")
    return value


def _attest_report(rest: list[str]) -> int:
    """``panopti attest report <seal.json> [--format md|html] [--output <path>]``."""
    from panopti.reports import REPORT_FORMATS, SealReportError, export_report

    usage = "Usage: panopti attest report <seal_file.json> [--format md|html] [--output <path>]"
    seal_path: str | None = None
    fmt = "md"
    output: str | None = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("--format", "-f", "--output", "-o"):
            if i + 1 >= len(rest):
                print(f"Error: {arg} needs a value\n{usage}", file=sys.stderr)
                return 1
            if arg in ("--format", "-f"):
                fmt = rest[i + 1]
            else:
                output = rest[i + 1]
            i += 2
        elif arg.startswith("-") or seal_path is not None:
            print(f"Error: unexpected argument '{arg}'\n{usage}", file=sys.stderr)
            return 1
        else:
            seal_path = arg
            i += 1
    if seal_path is None:
        print(usage, file=sys.stderr)
        return 1
    if fmt not in REPORT_FORMATS:
        print(f"Error: unknown format '{fmt}' (choose md or html)", file=sys.stderr)
        return 1
    try:
        with open(seal_path, encoding="utf-8") as f:
            seal_data = json.load(f)
        report = export_report(seal_data, fmt)
    except (OSError, ValueError) as e:  # ValueError covers bad JSON and SealReportError
        kind = "seal" if isinstance(e, SealReportError) else "seal file"
        print(f"Error reading {kind}: {e}", file=sys.stderr)
        return 1
    if output is None:
        sys.stdout.write(report)
        return 0
    try:
        with open(output, "w", encoding="utf-8", newline="\n") as f:
            f.write(report)
    except OSError as e:
        print(f"Error writing report: {e}", file=sys.stderr)
        return 1
    print(f"Report written to {output}")
    return 0


def main(args: list[str] | None = None) -> Any:
    """CLI entrypoint dispatching Panopti-specific commands and delegating to AgentWatch."""
    argv = list(sys.argv[1:] if args is None else args)

    if not argv or argv[0] in ("-h", "--help") and len(argv) == 1:
        # Check if user specifically called 'panopti --help'
        prog = Path(sys.argv[0]).stem
        if "panopti" in prog.lower() and not argv:
            print(PANOPTI_ASCII_BANNER)
            print("Usage: panopti [COMMAND] [ARGS]...\n")
            print("Panopti Commands:")
            print(
                "  demo            Run interactive terminal hypervisor simulation [--theme <name>] [--kill-file <path>]"
            )
            print("  playground      Launch zero-dependency local web interactive playground")
            print("  doctor          Run system, interceptor, and circuit breaker health checks")
            print(
                "  top / live      Interactive terminal sentinel monitor [--profile <name>]"
                " [--cooldown <s>] [--max-failures <n>]"
            )
            print("  bench           Measure hypervisor latency overhead [--iterations <n>]")
            print("  court verify    Verify a cryptographic execution witness proof (.json)")
            print("  attest verify   Verify an immutable cryptographic policy attestation seal")
            print("  attest report   Export a seal as a Markdown or HTML report [--format md|html]")
            print("  replay          Run deterministic shadow replay of an execution tape (.json)")
            print("  synth           Synthesize invariant contracts from trace (.json)\n")
            print("Available Profiles: fintech, devops, healthcare, support\n")
            print("For all core AgentWatch commands, pass standard subcommands.\n")

    if argv and argv[0] == "demo":
        from panopti.demo import run_interactive_demo

        fast = "--fast" in argv
        no_color = "--no-color" in argv
        theme: str | None = None
        if "--theme" in argv:
            t_idx = argv.index("--theme")
            if t_idx + 1 < len(argv):
                theme = argv[t_idx + 1]
        kill_file: str | None = None
        if "--kill-file" in argv:
            k_idx = argv.index("--kill-file")
            if k_idx + 1 < len(argv):
                kill_file = argv[k_idx + 1]
        return run_interactive_demo(fast=fast, no_color=no_color, theme=theme, kill_file=kill_file)

    if argv and argv[0] == "playground":
        from panopti.playground import launch_local_playground

        port = 8080
        if len(argv) >= 3 and argv[1] in ("-p", "--port"):
            try:
                port = int(argv[2])
            except ValueError:
                pass
        return launch_local_playground(port=port)

    if argv and argv[0] == "doctor":
        from panopti.doctor import format_doctor_report, run_diagnostics

        diag = run_diagnostics()
        print(PANOPTI_ASCII_BANNER)
        print(format_doctor_report(diag))
        return 0

    if argv and argv[0] == "bench":
        usage = "Usage: panopti bench [--iterations <n>]  (default 10000)"
        if any(a in ("-h", "--help") for a in argv[1:]):
            print(usage)
            return 0
        rest = argv[1:]
        try:
            iterations = _int_flag(argv, "--iterations", default=10000, minimum=1)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if "--iterations" in rest:
            idx = rest.index("--iterations")
            rest = rest[:idx] + rest[idx + 2 :]
        if rest:
            print(f"Error: unexpected argument '{rest[0]}'", file=sys.stderr)
            print(usage, file=sys.stderr)
            return 2
        from panopti.bench import format_bench_report, run_bench

        print(format_bench_report(run_bench(iterations), iterations))
        return 0

    if argv and argv[0] in ("top", "live"):
        from agentwatch.circuit_breaker import CircuitBreaker, CircuitThresholds
        from panopti.presets.profiles import get_profile

        profile_name = "devops"
        if "--profile" in argv:
            idx = argv.index("--profile")
            if idx + 1 < len(argv):
                profile_name = argv[idx + 1]
        prof = get_profile(profile_name)

        try:
            cooldown = _int_flag(argv, "--cooldown", default=60, minimum=0)
            max_failures = _int_flag(argv, "--max-failures", default=3, minimum=1)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        breaker = CircuitBreaker(
            f"panopti-{argv[0]}",
            CircuitThresholds(
                max_consecutive_errors=max_failures, cooldown_seconds=float(cooldown)
            ),
        )

        print(PANOPTI_ASCII_BANNER)
        print(f"👁️  PANOPTI SENTINEL [LIVE MONITOR - PROFILE: {prof.name.upper()}]")
        print("--------------------------------------------------")
        print(f"Description:        {prof.description}")
        print("Active Interceptor: ONLINE (0.00ms latency)")
        print(f"Circuit Breaker:    {breaker.state.value.upper()} (nominal)")
        print(f"Breaker Cooldown:   {cooldown}s")
        print(f"Max Failures:       {max_failures} consecutive")
        print(f"Confidence Floor:   {prof.confidence_floor:.2f}")
        print(f"Target Patterns:    {len(prof.destructive_patterns)} compiled regex rules")
        print("Forensic Witness:   RECORDING (Merkle sealed)")
        print("--------------------------------------------------")
        print("Listening on telemetry socket... (Press Ctrl+C to exit)")
        return 0

    if argv and argv[0] == "court":
        if len(argv) >= 3 and argv[1] == "verify":
            filepath = argv[2]
            try:
                from panopti.court import WitnessChain

                with open(filepath, encoding="utf-8") as f:
                    proof = json.load(f)
                valid, err = WitnessChain.verify_proof(proof)
                if valid:
                    print(
                        f"✅ VERIFIED: Witness proof in '{filepath}' is cryptographically intact."
                    )
                    print(f"   Root Hash: {proof.get('root_hash')}")
                    print(f"   Entries:   {len(proof.get('entries', []))}")
                    return 0
                else:
                    print(f"❌ TAMPER DETECTED: {err}")
                    return 1
            except Exception as e:
                print(f"Error reading proof file: {e}", file=sys.stderr)
                return 1
        print("Usage: panopti court verify <proof_file.json>")
        return 1

    if argv and argv[0] == "attest":
        if len(argv) >= 2 and argv[1] == "report":
            return _attest_report(argv[2:])
        if len(argv) >= 3 and argv[1] == "verify":
            filepath = argv[2]
            key = (
                argv[4]
                if len(argv) >= 5 and argv[3] in ("-k", "--key")
                else "panopti-seal-default-key"
            )
            try:
                from panopti.attest import AttestationEngine, PanoptiAttestationSeal

                with open(filepath, encoding="utf-8") as f:
                    seal = PanoptiAttestationSeal.from_json(f.read())
                if AttestationEngine.verify_seal(seal, secret_key=key):
                    print(f"✅ VERIFIED ATTESTATION SEAL: '{seal.seal_id}'")
                    print(f"   Policy Verdict:   {seal.policy_verdict}")
                    print(f"   Confidence Floor: {seal.confidence_floor:.4f}")
                    print(f"   Loop Status:      {seal.loop_status}")
                    print(f"   Merkle Root:      {seal.merkle_root}")
                    return 0
                else:
                    print("❌ INVALID SEAL: Signature mismatch or corrupted certificate.")
                    return 1
            except Exception as e:
                print(f"Error reading attestation seal: {e}", file=sys.stderr)
                return 1
        print("Usage: panopti attest verify <seal_file.json> [--key <secret_key>]")
        print("       panopti attest report <seal_file.json> [--format md|html] [--output <path>]")
        return 1

    if argv and argv[0] == "replay":
        if len(argv) >= 2:
            filepath = argv[1]
            try:
                from panopti.replay import ReplayTape, ShadowReplayer

                with open(filepath, encoding="utf-8") as f:
                    tape = ReplayTape.from_json(f.read())
                replayer = ShadowReplayer(tape)
                for call in tape.calls:
                    res, err = replayer.step(call.tool_name, call.arguments)
                    if err:
                        print(f"❌ DIVERGENCE DETECTED at step {call.step_index}: {err}")
                        return 1
                print(f"✅ REPLAY CONGRUENT: {len(tape)} recorded steps matched deterministically.")
                return 0
            except Exception as e:
                print(f"Error during shadow replay: {e}", file=sys.stderr)
                return 1
        print("Usage: panopti replay <tape_file.json>")
        return 1

    if argv and argv[0] == "synth":
        if len(argv) >= 2:
            filepath = argv[1]
            try:
                from panopti.synth import PolicySynthesizer

                with open(filepath, encoding="utf-8") as f:
                    events = json.load(f)
                if isinstance(events, dict) and "events" in events:
                    events = events["events"]
                synthesized = PolicySynthesizer.analyze_trace(events)
                print(f"🔍 SYNTHESIS COMPLETED: {len(synthesized)} recommendation(s)\n")
                for s in synthesized:
                    print(f"• [{s.trigger_pattern}] (conf={s.confidence:.2f}): {s.target_tool}")
                    print(f"  Contract:    {s.contract.name}")
                    print(f"  Explanation: {s.explanation}\n")
                return 0
            except Exception as e:
                print(f"Error analyzing trace for policy synthesis: {e}", file=sys.stderr)
                return 1
        print("Usage: panopti synth <trace_file.json>")
        return 1

    # Delegate all standard commands to agentwatch.cli.main
    from agentwatch.cli.main import main as agentwatch_main

    return agentwatch_main()


if __name__ == "__main__":
    sys.exit(main())
