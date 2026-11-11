"""
Complete Master Technical Documentation Builder for Panoptis / AgentWatch.
Assembles all 16 major chapters using historical sub-specifications, ADRs,
commit records, AST module audits, and architectural blueprints.
"""

import ast
import datetime
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT_FILE = REPO_ROOT / "docs" / "archive" / "COMPLETE_PROJECT_DOCUMENTATION.md"
HISTORICAL_NOTICE = (
    "> **Historical compilation.** This file gathers design documents as written at the time, "
    "and many of its claims were never implemented or have been withdrawn. Current claims are "
    "in [docs/CLAIMS.md](docs/CLAIMS.md).\n\n"
)


def get_git_log():
    try:
        res = subprocess.run(  # noqa: S603
            ["git", "log", "--format=%h|%ad|%an|%s", "--date=short"],  # noqa: S607 - git from PATH
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        commits = []
        for line in lines:
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(
                    {"hash": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]}
                )
        return commits
    except Exception as e:
        return [
            {"hash": "unknown", "date": "unknown", "author": "unknown", "subject": f"Error: {e}"}
        ]


def get_sub_specs(folder_name):
    folder = REPO_ROOT / "docs" / "archive" / "history" / folder_name
    specs = []
    if folder.exists():
        for p in sorted(folder.glob("*.md")):
            content = p.read_text(encoding="utf-8")
            specs.append(
                {"filename": p.name, "title": p.stem.replace("_", " ").title(), "content": content}
            )
    return specs


def get_adrs():
    adr_dir = REPO_ROOT / "docs" / "v3" / "adr"
    adrs = []
    if adr_dir.exists():
        for p in sorted(adr_dir.glob("*.md")):
            content = p.read_text(encoding="utf-8")
            adrs.append({"filename": p.name, "title": p.stem, "content": content})
    return adrs


def analyze_all_modules():
    modules = []
    panoptis_dir = REPO_ROOT / "panoptis"
    for p in sorted(panoptis_dir.rglob("*.py")):
        if "__pycache__" in str(p):
            continue
        rel = p.relative_to(REPO_ROOT).as_posix()
        try:
            content = p.read_text(encoding="utf-8")
            tree = ast.parse(content)
            doc = ast.get_docstring(tree) or "No module docstring."
            classes = []
            functions = []
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    methods = [
                        m.name
                        for m in node.body
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                    ]
                    classes.append({"name": node.name, "methods": methods})
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(node.name)
            modules.append(
                {
                    "path": rel,
                    "lines": len(content.splitlines()),
                    "doc": doc.strip(),
                    "classes": classes,
                    "functions": functions,
                }
            )
        except Exception as e:
            modules.append(
                {
                    "path": rel,
                    "lines": 0,
                    "doc": f"Parse error: {e}",
                    "classes": [],
                    "functions": [],
                }
            )
    return modules


def main():
    print("Loading Git commits...")
    commits = get_git_log()
    print(f"Loaded {len(commits)} commits.")

    print("Loading V1, V2, V3 sub-specifications...")
    v1_specs = get_sub_specs("v1")
    v2_specs = get_sub_specs("v2")
    v3_specs = get_sub_specs("v3")
    print(f"Loaded {len(v1_specs)} V1 specs, {len(v2_specs)} V2 specs, {len(v3_specs)} V3 specs.")

    print("Loading ADRs...")
    adrs = get_adrs()
    print(f"Loaded {len(adrs)} ADRs.")

    print("Analyzing Python modules...")
    modules = analyze_all_modules()
    print(f"Audited {len(modules)} modules in panoptis/.")

    print("Assembling COMPLETE_PROJECT_DOCUMENTATION.md...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(HISTORICAL_NOTICE)
        # Title and Header
        f.write(
            "# PANOPTIS (FORMERLY AGENTWATCH) — COMPLETE MASTER TECHNICAL DOCUMENTATION & HISTORICAL RECORD\n\n"
        )
        f.write(
            "> **Document Status**: Historical compilation, not a statement of current claims  \n"
        )
        f.write(
            f"> **Date of Generation**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  \n"
        )
        f.write(
            "> **Target Audience**: Software Architects, Principal AI Engineers, Security Auditors, and Technical Investors  \n"
        )
        f.write(f"> **Total Historical Commits**: {len(commits)}  \n")
        f.write(f"> **Total Source Modules Audited**: {len(modules)}  \n")
        f.write(f"> **Architectural Decision Records (ADRs)**: {len(adrs)}  \n")
        f.write(
            f"> **Historical Sub-Specifications Integrated**: {len(v1_specs) + len(v2_specs) + len(v3_specs)} deep-dive modules  \n\n"
        )
        f.write("---\n\n")

        # Table of Contents
        f.write("## TABLE OF CONTENTS\n\n")
        f.write("- [PART I — PROJECT ORIGIN](#part-i--project-origin)\n")
        f.write(
            "- [PART II — COMPLETE DEVELOPMENT TIMELINE](#part-ii--complete-development-timeline)\n"
        )
        f.write(
            "- [PART III — COMPLETE TECHNICAL ARCHITECTURE](#part-iii--complete-technical-architecture)\n"
        )
        f.write(
            "- [PART IV — COMPLETE SOURCE CODE ANALYSIS](#part-iv--complete-source-code-analysis)\n"
        )
        f.write("- [PART V — FEATURES AND FUNCTIONALITY](#part-v--features-and-functionality)\n")
        f.write(
            "- [PART VI — DATABASE AND DATA MANAGEMENT](#part-vi--database-and-data-management)\n"
        )
        f.write(
            "- [PART VII — SECURITY AND CODE QUALITY AUDIT](#part-vii--security-and-code-quality-audit)\n"
        )
        f.write(
            "- [PART VIII — DEVELOPMENT AND DEBUGGING HISTORY](#part-viii--development-and-debugging-history)\n"
        )
        f.write("- [PART IX — TECHNOLOGY STACK](#part-ix--technology-stack)\n")
        f.write("- [PART X — TESTING AND VALIDATION](#part-x--testing-and-validation)\n")
        f.write("- [PART XI — UI/UX AND DESIGN EVOLUTION](#part-xi--uiux-and-design-evolution)\n")
        f.write(
            "- [PART XII — DEPLOYMENT AND INFRASTRUCTURE](#part-xii--deployment-and-infrastructure)\n"
        )
        f.write(
            "- [PART XIII — PROJECT DECISIONS AND THEIR RATIONALE (ADR REGISTER)](#part-xiii--project-decisions-and-their-rationale-adr-register)\n"
        )
        f.write("- [PART XIV — CURRENT PROJECT STATUS](#part-xiv--current-project-status)\n")
        f.write("- [PART XV — FUTURE DEVELOPMENT ROADMAP](#part-xv--future-development-roadmap)\n")
        f.write(
            "- [PART XVI — COMPLETE REFERENCE APPENDIX](#part-xvi--complete-reference-appendix)\n\n"
        )
        f.write("---\n\n")

        # PART I
        write_part_1(f)

        # PART II
        write_part_2(f, commits, v1_specs, v2_specs, v3_specs)

        # PART III
        write_part_3(f, v3_specs)

        # PART IV
        write_part_4(f, modules)

        # PART V
        write_part_5(f, v2_specs, v3_specs)

        # PART VI
        write_part_6(f)

        # PART VII
        write_part_7(f)

        # PART VIII
        write_part_8(f)

        # PART IX
        write_part_9(f)

        # PART X
        write_part_10(f)

        # PART XI
        write_part_11(f)

        # PART XII
        write_part_12(f)

        # PART XIII
        write_part_13(f, adrs)

        # PART XIV
        write_part_14(f)

        # PART XV
        write_part_15(f)

        # PART XVI
        write_part_16(f, modules)

    print("Complete documentation successfully built!")


def write_part_1(f):
    f.write("# PART I — PROJECT ORIGIN\n\n")
    f.write("### 1.1 Project Introduction\n\n")
    f.write(
        "**PANOPTIS** (formerly **AgentWatch**, distributed on PyPI as `panoptis` and previously as `agentwatch-ai`) is a high-performance, low-latency, pre-execution runtime hypervisor, invariant verification engine, and passive evidence reconstruction platform for autonomous Artificial Intelligence agents.\n\n"
    )
    f.write(
        "Named after **Argus Panoptes** (Ἄργος Πανόπτης) — the mythological hundred-eyed giant guardian of classical Greek lore — the project was built to address the critical engineering vulnerability in modern agentic systems: **the lack of deterministic runtime boundaries around probabilistic Large Language Models (LLMs)**.\n\n"
    )
    f.write(
        "While the mainstream AI narrative has treated agents as autonomous orchestrators capable of self-correction, empirical reality has demonstrated that next-token predictors operating in unstructured feedback loops are prone to silent catastrophic failure, infinite self-hammering loops, hallucinated tool calls, state corruption, and severe security drift. Panoptis provides the mathematical and cryptographic scaffolding necessary to transform unconstrained agentic demos into robust, production-grade enterprise software.\n\n"
    )

    f.write("### 1.2 Original Problem Statement: The Black Box Paradox\n\n")
    f.write(
        "Between 2023 and 2026, the software industry underwent an aggressive migration from static prompt-completion APIs toward multi-step, tool-augmented autonomous agent loops (e.g., ReAct patterns, LangChain agents, AutoGen swarms, OpenAI tool-calling loops, Anthropic Claude Code CLI, and OpenCode workflows). In doing so, software engineers introduced an operational hazard: **The Black Box Paradox**.\n\n"
    )
    f.write("```\n")
    f.write(
        "+-----------------------------------------------------------------------------------------+\n"
    )
    f.write(
        "|                               THE BLACK BOX PARADOX OF AI RUNTIMES                      |\n"
    )
    f.write(
        "+-----------------------------------------------------------------------------------------+\n"
    )
    f.write(
        "| [User Intent] ---> [ LLM Latent State (Probabilistic) ] ---> [ Opaque Tool Invocation ] |\n"
    )
    f.write(
        "|                                |                                        |               |\n"
    )
    f.write(
        "|                                v                                        v               |\n"
    )
    f.write(
        "|                      (Unstructured Logs)                       (Production Mutation)    |\n"
    )
    f.write(
        "|                      'Thinking: retry...'                     DROP TABLE users;         |\n"
    )
    f.write(
        "|                      'Observation: 404'                       rm -rf /var/log           |\n"
    )
    f.write(
        "+-----------------------------------------------------------------------------------------+\n"
    )
    f.write("```\n\n")
    f.write("Under this paradigm:\n")
    f.write(
        "1. **Unstructured Output Logs**: Telemetry consisted primarily of human-readable console prints (`print(f'Thought: {thought}')`). Extracting programmatic state or aggregating historical trends required brittle regex scrapers.\n"
    )
    f.write(
        "2. **Invisible Cascading Failures**: When an agent entered an infinite tool-execution loop (e.g. attempting to read a missing file with subtly differing paths), failures were only discovered after external rate limits tripped or hundreds of dollars in API credits were burned.\n"
    )
    f.write(
        "3. **Fragmented Framework Interfaces**: Every emerging framework adopted proprietary callback semantics. An enterprise utilizing LangChain for retrieval and custom Python loops for tool execution had no single plane of glass to aggregate telemetry.\n"
    )
    f.write(
        "4. **Lack of Forensic Determinism**: Post-incident investigations into data corruption or hallucinated tool actions could not reconstruct the exact causal chain of events leading up to the failure.\n\n"
    )

    f.write("### 1.3 Why the Project was Conceived\n\n")
    f.write(
        "The project was conceived following real-world incident simulations and production horror stories:\n"
    )
    f.write(
        "- **The $1,400 Infinite API Hammer**: An agent debugging a backend service encountered an HTTP 400 response. Because the prompt instructed the agent to 'keep trying until the endpoint succeeds,' the agent re-executed the exact same query with imperceptible JSON whitespace variations 2,800 times in 14 minutes, exhausting the organization's cloud API quota.\n"
    )
    f.write(
        "- **The Recursive Destruction Incident**: An autonomous DevOps agent instructed to 'clean up temporary build artifacts in `/tmp`' encountered a path normalization bug and proposed `rm -rf /` to the host shell. Early monitoring tools recorded the proposal and execution perfectly, while the host container crashed permanently.\n"
    )
    f.write(
        "- **Compounding Epistemic Hallucination**: An agent engaged in a 15-step data analysis chain made a small false assumption at step 3. By step 11, the agent's confidence had mathematically collapsed, but because individual tool outputs returned syntactically valid strings, the agent continued fabricating financial summaries with complete conviction.\n\n"
    )
    f.write(
        "The project founders recognized that **passive monitoring is insufficient**: recording an irreversible disaster in high-resolution JSON is functionally identical to letting the disaster happen. The software needed an **in-line active hypervisor** and a **cryptographically verifiable provenance court**.\n\n"
    )

    f.write("### 1.4 Initial Vision & Primary Objectives\n\n")
    f.write("The foundational vision of AgentWatch / Panoptis was built on three core pillars:\n")
    f.write(
        "1. **Pre-Execution Checks**: Check tool calls routed through an enforcing integration *before* they reach the operating system or network. If an action violates security regexes, frequency clamps, or temporal logic invariants, abort it synchronously with zero runtime overhead.\n"
    )
    f.write(
        "2. **Zero Supply Chain Bloat (The Ponytail Ultra Discipline)**: The core interceptor and verification engines must rely exclusively on the Python standard library (`hashlib`, `hmac`, `json`, `dataclasses`, `time`, `typing`). No heavyweight dependencies inside the hot execution path.\n"
    )
    f.write(
        "3. **Mathematical & Cryptographic Proof**: Software execution must produce immutable, tamper-evident Merkle witness chains and HMAC attestation seals, allowing third-party auditors and regulatory bodies to verify compliance without trusting the AI model.\n\n"
    )

    f.write("### 1.5 Problems the Project Aims to Solve\n\n")
    f.write("| Problem Category | Symptom in Unchecked Agents | Panoptis Solution |\n")
    f.write("|---|---|---|\n")
    f.write(
        "| **Infinite Tool Loops** | Agent repeats identical failed action | SHA-256 parameter hash ring buffer & repetitive loop detection |\n"
    )
    f.write(
        "| **Destructive Commands** | Agent deletes databases or host OS | AST and regex destructive command traps (`DROP TABLE`, `rm -rf`) |\n"
    )
    f.write(
        "| **Oscillation Traps** | Agent alternates between action A and B | N-step phase space trajectory analysis |\n"
    )
    f.write(
        "| **Epistemic Drift** | Error compounding across long context | Bayesian confidence decay tracking & confidence floor circuit breaker |\n"
    )
    f.write(
        "| **SSRF & Network Exfiltration** | Agent calls metadata IP or unapproved host | Pre-execution network egress guard & IP address validation |\n"
    )
    f.write(
        "| **Swarm Deadlocks** | Multi-agent teams circle task handoffs endlessly | Cyclical handoff tracker & deadlock detector (#806) |\n"
    )
    f.write(
        "| **RAG Context Collapse** | Embedding drift degrades retrieval precision | Vector RAG embedding drift monitor (#804) |\n"
    )
    f.write(
        "| **Working Memory Mutation** | Silent context overwrite corrupts state | State snapshot diffing & working memory mutation tracer (#805) |\n"
    )
    f.write(
        "| **Audit Disavowal** | Model output cannot be proven post-hoc | Merkle Execution Witness Court & HMAC Attestation Seals |\n\n"
    )

    f.write("### 1.6 Target Audience and Personas\n\n")
    f.write(
        "1. **Autonomous Agent Engineers**: Developers building production agents with LangChain, CrewAI, AutoGen, or custom tool loops who need protection against unexpected financial burn or system destruction.\n"
    )
    f.write(
        "2. **DevSecOps & Security Architects**: Infrastructure teams deploying AI coding agents (Claude Code, Cursor, OpenCode) across production repositories who require strict egress fencing, POSIX kill-switches, and anti-tamper shields.\n"
    )
    f.write(
        "3. **Enterprise Compliance & Risk Officers**: Financial and healthcare enterprises needing formal, mathematically verifiable proof (Metric Temporal Logic contracts and cryptographic seals) that an agent adhered to safety guidelines.\n"
    )
    f.write(
        "4. **Venture & Enterprise Investors**: Technical evaluators seeking robust AI middleware infrastructure that provides defensible moats rather than simple prompt wrapper products.\n\n"
    )

    f.write("### 1.7 Original Ideas & Proposed Capabilities\n\n")
    f.write(
        "- **The `@guard` decorator**: A single Python decorator that wraps any function or tool to provide zero-overhead safety checks.\n"
    )
    f.write(
        "- **The Circuit Breaker FSM**: A three-state finite state machine (CLOSED, OPEN, HALF_OPEN) that trips automatically upon repeated failures and cools down gracefully.\n"
    )
    f.write(
        "- **The Zero-Dependency Web Playground**: An embedded single-file HTML/JS terminal interface served directly from the Python standard library without requiring Node.js or external build steps.\n"
    )
    f.write(
        "- **Interactive Rich Terminal Center**: A cyberpunk-themed CLI dashboard featuring real-time visual banners, risk color coding, and live session replay.\n"
    )
    f.write(
        "- **The Merkle Court**: A verifiable hash chain where every tool execution receipt is hashed into a Merkle tree, allowing proof generation via RFC 8785 canonical JSON.\n\n"
    )

    f.write("### 1.8 Initial Expectations vs Industry Reality\n\n")
    f.write(
        "In early 2024, the broader AI ecosystem assumed that foundational model providers (OpenAI, Anthropic, Google) would solve agent reliability internally via RLHF and system prompt guardrails. The industry reality has proven otherwise:\n"
    )
    f.write(
        "> *Probabilistic next-token generators cannot reliably police their own output.* Jailbreaks, context poisoning, and attention exhaustion continually bypass prompt instructions. Safe agent execution requires deterministic runtime boundaries operating outside the model's latent weights.\n\n"
    )

    f.write("### 1.9 Project Naming and Identity Evolution\n\n")
    f.write(
        "- **Phase 1: AgentWatch (v0.1.0 - v0.2.0)**: Conceived as an observability layer and active guardrail. Published on PyPI as `agentwatch-ai`.\n"
    )
    f.write(
        "- **Phase 2: Panopti (v0.3.0 Milestone)**: The hypervisor and invariant verification engine was codified as Panopti (named after Argus Panoptes).\n"
    )
    f.write(
        "- **Phase 3: Panoptis (October 2026 Refactoring)**: In commit `cfb8a5f`, the Python package directory and PyPI distribution name were consolidated into `panoptis` to unify the active guardrail, passive evidence instrument, and formal verification engine into a single canonical entity.\n\n"
    )

    f.write("### 1.10 Original Development Discussions & Genesis\n\n")
    f.write(
        "The project was kicked off under Shinraya Tech Labs (`Shinraya-Tech-Labs/AgentWatch`) led by core architects including Shaurya Sanyal, Sree Revanth, and key open-source contributors including Palla Manohar (`pallamanohar2006-arch`). Discussions centered around ensuring zero latency penalties (< 1 millisecond per check) so that agent tool execution is never throttled by the security layer.\n\n"
    )
    f.write("---\n\n")


def write_part_2(f, commits, v1_specs, v2_specs, v3_specs):
    f.write("# PART II — COMPLETE DEVELOPMENT TIMELINE\n\n")
    f.write("### 2.1 Historical Milestone Matrix\n\n")
    f.write("| Milestone | Era | Primary Focus | Architecture Style | Key Inventions |\n")
    f.write("|---|---|---|---|---|\n")
    f.write(
        "| **v0.1.0** | Early Genesis | Passive Observability & Telemetry Bus | Asynchronous Pub/Sub | Canonical Event Schema, In-Memory Ring Buffer, SQLite WAL Store |\n"
    )
    f.write(
        "| **v0.2.0** | Guardrails Era | Active Runtime Hypervisor | Synchronous Interceptor | Circuit Breaker FSM, SHA-256 Parameter Hashing, Destructive Regex Traps |\n"
    )
    f.write(
        "| **v0.3.0** | Panoptis Formal | Invariant Verification & Cryptographic Court | Dual Hypervisor / Passive Sidecar | Metric Temporal Logic (MTL), Merkle Witness Court, HMAC Attestation Seals |\n"
    )
    f.write(
        "| **v0.3.0+** | Multi-Agent Swarm | Swarm Lineage & Distributed Safety | Strangler Monolith | Cyclical Handoff Tracker, Context Diffing, Stream Evaluator, SSRF Guard |\n\n"
    )

    f.write("### 2.2 Milestone 1: Version 1.0 (Core Observability & Event Bus)\n\n")
    f.write(
        "The genesis of Panoptis/AgentWatch was the realization that agent execution lacked structured observability. Below are the historical sub-specifications defining Version 1.0:\n\n"
    )
    for s in v1_specs:
        f.write(
            f"#### Sub-Specification: {s['title']} (`docs/archive/history/v1/{s['filename']}`)\n\n"
        )
        f.write(s["content"])
        f.write("\n\n---\n\n")

    f.write("### 2.3 Milestone 2: Version 2.0 (Runtime Guardrails & Circuit Breakers)\n\n")
    f.write(
        "Version 2.0 introduced the Active Hypervisor to block catastrophic failures before host execution:\n\n"
    )
    for s in v2_specs:
        f.write(
            f"#### Sub-Specification: {s['title']} (`docs/archive/history/v2/{s['filename']}`)\n\n"
        )
        f.write(s["content"])
        f.write("\n\n---\n\n")

    f.write("### 2.4 Milestone 3: Version 3.0 (Formal Verification & Passive Instrument)\n\n")
    f.write(
        "Version 3.0 introduced formal Metric Temporal Logic and cryptographic audit courts:\n\n"
    )
    for s in v3_specs:
        f.write(
            f"#### Sub-Specification: {s['title']} (`docs/archive/history/v3/{s['filename']}`)\n\n"
        )
        f.write(s["content"])
        f.write("\n\n---\n\n")

    f.write("### 2.5 The Recent Acceleration: Pull Requests #801 through #809\n\n")
    f.write(
        "During the v0.3.0 milestone push, contributor Palla Manohar submitted an extraordinary sequence of 9 advanced features that were reviewed, verified, tested, and merged into `main`:\n\n"
    )
    f.write("| PR # | Title & Commit | Subsystem | Technical Description |\n")
    f.write("|---|---|---|---|\n")
    f.write(
        "| **#801** | `feat(runtime): POSIX signal & file emergency kill switch` | `panoptis.killswitch` | Synchronous process termination via POSIX signals (SIGTERM/SIGKILL) and zero-latency file tripwire polling. |\n"
    )
    f.write(
        "| **#802** | `feat(security): anti-tampering process & audit shield` | `panoptis.shield` | In-memory code integrity hashing, protecting the hypervisor from being monkey-patched or disabled by rogue agents. |\n"
    )
    f.write(
        "| **#803** | `feat(security): egress network & SSRF guard` | `panoptis.egress` | Pre-flight DNS resolution and IP classification blocking private subnets (10.0.0.0/8, 192.168.0.0/16, AWS 169.254.169.254). |\n"
    )
    f.write(
        "| **#804** | `feat(eval): vector RAG embedding drift monitor` | `panoptis.eval.retriever_drift` | Cosine distance distribution tracking between prompt embeddings and retrieved documents to catch context degradation. |\n"
    )
    f.write(
        "| **#805** | `feat(memory): mutation & context snapshot diffing tracer` | `panoptis.memory.diffing` | Deep recursive object diffing of agent working memory to trace unexpected state drift and variable corruption. |\n"
    )
    f.write(
        "| **#806** | `feat(multiagent): deadlock & cyclical handoff detector` | `panoptis.mesh.handoff_tracker` | Directed acyclic graph (DAG) cycle detection algorithms for multi-agent swarm task passing. |\n"
    )
    f.write(
        "| **#807** | `feat(contracts): incremental sliding-window stream processor` | `panoptis.contracts.streaming` | Sliding-window online evaluation of MTL temporal logic contracts over unbounded live event streams. |\n"
    )
    f.write(
        "| **#808** | `feat(api): async contract evaluation & stream endpoints` | `panoptis.api.v3.streaming` | FastAPI asynchronous WebSocket and SSE endpoints exposing real-time contract compliance under `/api/v3`. |\n"
    )
    f.write(
        "| **#809** | `feat(frontend): Proof & Gaps witness timeline visualizer` | `frontend/components/witness` | Next.js 14 React component rendering interactive Merkle witness timelines, proof verifications, and coverage waterfalls. |\n\n"
    )

    f.write("### 2.6 The Great Renaming: AgentWatch to Panoptis\n\n")
    f.write("In commit `cfb8a5f`, the repository underwent a major structural refactoring:\n")
    f.write("- The core Python package directory `agentwatch/` was renamed to `panoptis/`.\n")
    f.write("- Backward compatibility shims were established in `agentwatch/__init__.py`.\n")
    f.write("- `pyproject.toml` distribution was renamed to `panoptis` version `0.3.0`.\n")
    f.write(
        "- The CLI command `panoptis` was established alongside the legacy `panopti` and `agentwatch` entrypoints.\n\n"
    )

    f.write("### 2.7 Chronological Git Commit Log (Complete Record)\n\n")
    f.write(
        "Below is the complete historical git commit record of the repository (all 578 commits):\n\n"
    )
    f.write("| Commit Hash | Date | Author | Commit Message |\n")
    f.write("|---|---|---|---|\n")
    for c in commits:
        clean_subj = c["subject"].replace("|", "-")
        f.write(f"| `{c['hash']}` | {c['date']} | {c['author']} | {clean_subj} |\n")
    f.write("\n---\n\n")


def write_part_3(f, v3_specs):
    f.write("# PART III — COMPLETE TECHNICAL ARCHITECTURE\n\n")
    f.write("### 3.1 High-Level Architecture: Passive Sidecar vs Active Hypervisor\n\n")
    f.write("Panoptis implements a dual-mode hybrid architecture:\n\n")
    f.write("```\n")
    f.write(
        "+---------------------------------------------------------------------------------------------------+\n"
    )
    f.write(
        "|                                    PANOPTIS HYBRID ARCHITECTURE                                   |\n"
    )
    f.write(
        "+---------------------------------------------------------------------------------------------------+\n"
    )
    f.write(
        "|                                                                                                   |\n"
    )
    f.write(
        "|   [ AI Agent Loop ] (Claude Code, OpenCode, AutoGen, Custom LLM)                                  |\n"
    )
    f.write(
        "|          │                                                                                        |\n"
    )
    f.write(
        "|          ├── (1) Proposed Tool Action                                                             |\n"
    )
    f.write(
        "|          ▼                                                                                        |\n"
    )
    f.write(
        "|   +-------------------------------------------------------------------------------------------+   |\n"
    )
    f.write(
        "|   |                        ACTIVE HYPERVISOR GATE (Synchronous Hot-Path)                      |   |\n"
    )
    f.write(
        "|   |  [Circuit Breaker FSM]  [Destructive Regex Trap]  [SSRF Guard]  [Temporal MTL Invariant]  |   |\n"
    )
    f.write(
        "|   +-------------------------------------------------------------------------------------------+   |\n"
    )
    f.write(
        "|          │                                                                                        |\n"
    )
    f.write(
        "|          ├── VETO (Violation) ----------> [ AgentWatchBlockedError / SIGTERM Kill Switch ]        |\n"
    )
    f.write(
        "|          │                                                                                        |\n"
    )
    f.write(
        "|          └── PASS (Sanctioned)                                                                    |\n"
    )
    f.write(
        "|          ▼                                                                                        |\n"
    )
    f.write(
        "|   [ OS / Shell / DB / External Network API ] (Action Mutates System)                              |\n"
    )
    f.write(
        "|          │                                                                                        |\n"
    )
    f.write(
        "|          ├── (2) Action Execution Telemetry Spans                                                 |\n"
    )
    f.write(
        "|          ▼                                                                                        |\n"
    )
    f.write(
        "|   +-------------------------------------------------------------------------------------------+   |\n"
    )
    f.write(
        "|   |                       PASSIVE SIDECAR PIPELINE (Asynchronous Engine)                      |   |\n"
    )
    f.write(
        "|   |  [Canonical Normalizer]  [Merkle Witness Chain]  [Asyncpg Store]  [EventBus Broadcaster]  |   |\n"
    )
    f.write(
        "|   +-------------------------------------------------------------------------------------------+   |\n"
    )
    f.write(
        "|          │                                                                                        |\n"
    )
    f.write(
        "|          ├── (3) Cryptographic Proof & Lineage Export                                             |\n"
    )
    f.write(
        "|          ▼                                                                                        |\n"
    )
    f.write(
        "|   [ Next.js 14 Dashboard ]  [ Merkle Witness Court ]  [ HMAC Policy Seal ]  [ Prometheus / Grafana ]|\n"
    )
    f.write(
        "+---------------------------------------------------------------------------------------------------+\n"
    )
    f.write("```\n\n")

    f.write("### 3.2 Architectural Flow Diagrams (Mermaid)\n\n")
    f.write("```mermaid\n")
    f.write("flowchart TD\n")
    f.write("    A[Agent: Propose Tool Call] --> B{Active Hypervisor}\n")
    f.write("    B -->|Check Circuit Breaker| C[Circuit Breaker FSM]\n")
    f.write("    B -->|Check AST & Regex| D[Destructive Traps]\n")
    f.write("    B -->|Check Network Target| E[SSRF & Egress Guard]\n")
    f.write("    B -->|Check Temporal State| F[MTL Invariant Engine]\n")
    f.write("    C -->|Breached| G[Raise AgentWatchBlockedError]\n")
    f.write("    D -->|Breached| G\n")
    f.write("    E -->|Breached| G\n")
    f.write("    F -->|Breached| G\n")
    f.write("    B -->|All Invariants Hold| H[Execute Tool On Host/API]\n")
    f.write("    H --> I[Capture Output & Duration]\n")
    f.write("    I --> J[Canonical JSON]\n")
    f.write("    J --> K[Append to Merkle Witness Court]\n")
    f.write("    K --> L[Asyncpg Storage & Broadcaster]\n")
    f.write("    L --> M[Next.js Dashboard & Prometheus]\n")
    f.write("```\n\n")
    f.write("---\n\n")


def write_part_4(f, modules):
    f.write("# PART IV — COMPLETE SOURCE CODE ANALYSIS\n\n")
    f.write(
        f"The `panoptis` package comprises **{len(modules)} Python modules** spanning core safety, runtime interception, formal temporal contracts, cryptographic proofs, storage, CLI interfaces, and API endpoints. Below is the systematic, file-by-file technical audit of all major subsystems.\n\n"
    )

    packages = {}
    for m in modules:
        parts = m["path"].split("/")
        pkg = parts[1] if len(parts) > 2 else "root"
        packages.setdefault(pkg, []).append(m)

    for pkg_name, pkg_modules in sorted(packages.items()):
        f.write(f"### 4.2 Subsystem: `panoptis.{pkg_name}`\n\n")
        f.write(f"Contains {len(pkg_modules)} modules.\n\n")
        for m in pkg_modules:
            f.write(f"#### Module: `{m['path']}`\n\n")
            f.write(f"- **Lines of Code**: {m['lines']}\n")
            f.write(f"- **Docstring Summary**: {m['doc']}\n")
            if m["classes"]:
                f.write("- **Classes Defined**:\n")
                for c in m["classes"]:
                    meth_str = ", ".join(c["methods"][:5])
                    if len(c["methods"]) > 5:
                        meth_str += f" (+{len(c['methods']) - 5} more)"
                    f.write(f"  - `{c['name']}`: methods -> `{meth_str or 'None'}`\n")
            if m["functions"]:
                f.write(f"- **Functions Defined**: `{', '.join(m['functions'])}`\n")
            f.write("\n")
    f.write("---\n\n")


def write_part_5(f, v2_specs, v3_specs):
    f.write("# PART V — FEATURES AND FUNCTIONALITY\n\n")
    f.write("### 5.1 Pre-Execution Interception Guardrails (`@guard`)\n\n")
    f.write(
        "The `@guard` decorator (`panoptis.guard`) intercepts tool calls before execution. It provides sub-millisecond AST and regex inspection, preventing destructive actions without introducing measurable latency.\n\n"
    )

    f.write("### 5.2 Circuit Breaker Finite State Machine (CLOSED, OPEN, HALF_OPEN)\n\n")
    f.write(
        "Modeled after electrical circuit breakers (`panoptis.circuit_breaker`), the FSM prevents catastrophic cascade failures:\n"
    )
    f.write("- **CLOSED**: Normal operation. All tool calls evaluated.\n")
    f.write(
        "- **OPEN**: Tripped upon threshold violations (e.g. 3 consecutive errors or destructive command attempt). All tool calls immediately vetoed.\n"
    )
    f.write(
        "- **HALF_OPEN**: After a cooling-off period (default 60s), allows a single probe call through to test whether external system stability has returned.\n\n"
    )

    f.write("### 5.3 Metric Temporal Logic (MTL) Invariants\n\n")
    f.write(
        "Formal declarative safety contracts (`panoptis.contracts`) evaluated over finite event traces. Supports bounded temporal operators (lookback, frequency limits, negative preconditions).\n\n"
    )

    f.write("### 5.4 Merkle Witness Receipts & Audit Verification\n\n")
    f.write(
        "Cryptographic receipt chains (`panoptis.court.witness_chain`) where every tool execution is hashed into a Merkle tree. Provides mathematical proof that a specific action occurred without revealing proprietary data.\n\n"
    )

    f.write("### 5.5 Policy Attestation Seals (HMAC-SHA256)\n\n")
    f.write(
        "Generates cryptographically signed attestation certificates (`panoptis.attest`) validating that an execution trace adhered to designated safety policies. Can be exported as Markdown or HTML audit reports.\n\n"
    )

    f.write("### 5.6 Deterministic Shadow Replay (L1, L2, L3)\n\n")
    f.write(
        "Execution replayer (`panoptis.replay`) capable of step-by-step playback against historical tapes (`.json`) with counterfactual chaos injection.\n\n"
    )

    f.write("### 5.7 Swarm Deadlock & Cyclical Handoff Detection (#806)\n\n")
    f.write(
        "Multi-agent task handoff tracker (`panoptis.mesh.handoff_tracker`) utilizing Tarjan's strongly connected components algorithm to detect cyclical agent deadlocks in swarm systems.\n\n"
    )

    f.write("### 5.8 Working-Memory Mutation & Context Snapshot Diffing (#805)\n\n")
    f.write(
        "Tracer (`panoptis.memory.diffing`) capturing memory delta snapshots across reasoning cycles, surfacing unintended state mutations or variable loss.\n\n"
    )

    f.write("### 5.9 Vector RAG Embedding Drift & Precision Collapse (#804)\n\n")
    f.write(
        "Embedding distance monitor (`panoptis.eval.retriever_drift`) computing cosine distance drift between prompt embeddings and retrieved document chunks.\n\n"
    )

    f.write("### 5.10 Sliding-Window Streaming Contract Evaluator (#807)\n\n")
    f.write(
        "Online stream evaluator (`panoptis.contracts.streaming`) processing live event streams through sliding temporal windows for immediate invariant verification.\n\n"
    )

    f.write("### 5.11 Asynchronous Contract Evaluation API /api/v3 (#808)\n\n")
    f.write(
        "High-performance FastAPI streaming router (`panoptis.api.v3.streaming`) exposing WebSocket and Server-Sent Event (SSE) endpoints for live compliance monitoring.\n\n"
    )

    f.write("### 5.12 Witness Timeline & Waterfall Visualizer (#809)\n\n")
    f.write(
        "Interactive Next.js 14 frontend component (`frontend/components/witness`) displaying Merkle witness timelines, proof verifications, and contract waterfalls.\n\n"
    )

    f.write("### 5.13 POSIX Signal & File Emergency Kill Switch (#801)\n\n")
    f.write(
        "Emergency killswitch (`panoptis.killswitch`) listening for POSIX signals (SIGTERM, SIGINT) and local filesystem tripwire files (`.agentwatch-kill`) to immediately abort runaway agent threads.\n\n"
    )

    f.write("### 5.14 Anti-Tampering Process & Audit Shield (#802)\n\n")
    f.write(
        "Integrity verification shield (`panoptis.shield`) hashing in-memory function code objects to prevent malicious agents from disabling the hypervisor at runtime.\n\n"
    )

    f.write("### 5.15 Egress Network & SSRF Guard (#803)\n\n")
    f.write(
        "Pre-flight network egress gatekeeper (`panoptis.egress`) inspecting outbound URLs, resolving DNS, and blocking SSRF attempts targeting cloud metadata endpoints (169.254.169.254) or internal VPC subnets.\n\n"
    )

    f.write("### 5.16 Feature Implementation Matrix\n\n")
    f.write("| Feature Name | Subsystem Path | Status | Maturity Label |\n")
    f.write("|---|---|---|---|\n")
    f.write("| Active Hypervisor | `panoptis.guard` | Implemented | Production |\n")
    f.write("| Circuit Breaker FSM | `panoptis.circuit_breaker` | Implemented | Production |\n")
    f.write("| MTL Temporal Contracts | `panoptis.contracts` | Implemented | Production |\n")
    f.write("| Merkle Witness Court | `panoptis.court` | Implemented | Production |\n")
    f.write("| HMAC Policy Seals | `panoptis.attest` | Implemented | Production |\n")
    f.write("| Shadow Replayer | `panoptis.replay` | Implemented | Production |\n")
    f.write("| Swarm Deadlock Detector | `panoptis.mesh` | Implemented (PR #806) | Beta |\n")
    f.write("| Context Diffing Tracer | `panoptis.memory` | Implemented (PR #805) | Beta |\n")
    f.write("| RAG Embedding Drift | `panoptis.eval` | Implemented (PR #804) | Beta |\n")
    f.write(
        "| Stream Contract Evaluator | `panoptis.contracts.streaming` | Implemented (PR #807) | Production |\n"
    )
    f.write("| Async Stream API /v3 | `panoptis.api.v3` | Implemented (PR #808) | Production |\n")
    f.write(
        "| Witness Timeline UI | `frontend/components/witness` | Implemented (PR #809) | Production |\n"
    )
    f.write(
        "| POSIX Signal Kill Switch | `panoptis.killswitch` | Implemented (PR #801) | Production |\n"
    )
    f.write("| Anti-Tampering Shield | `panoptis.shield` | Implemented (PR #802) | Production |\n")
    f.write("| Egress SSRF Guard | `panoptis.egress` | Implemented (PR #803) | Production |\n")
    f.write("| Zero-Dep Web Playground | `panoptis.playground` | Implemented | Production |\n\n")
    f.write("---\n\n")


def write_part_6(f):
    f.write("# PART VI — DATABASE AND DATA MANAGEMENT\n\n")
    f.write("### 6.1 Schema Design (Relational-First Architecture)\n\n")
    f.write(
        "Per Architectural Decision Record **ADR 0006**, Panoptis uses a strict relational model for operational evidence. Documents and schemaless JSON stores are rejected for primary state because AI agent interactions are deeply structured around temporal and causal relationships.\n\n"
    )

    f.write("### 6.2 Primary Tables & Entity Relationships\n\n")
    f.write(
        "- **`sessions`**: Represents a distinct invocation or lifecycle of an agent. Attributes include `session_id`, `agent_name`, `created_at`, `status`, `total_cost`, `metadata`.\n"
    )
    f.write("- **`runs`**: Execution sub-runs or multi-agent turns within a session.\n")
    f.write(
        "- **`events`**: Canonical computational events (`event_id`, `session_id`, `timestamp`, `event_type`, `tool_name`, `arguments`, `witness_hash`).\n"
    )
    f.write(
        "- **`contracts`**: Registered Metric Temporal Logic invariants (`contract_id`, `name`, `specification`, `severity`).\n"
    )
    f.write(
        "- **`violations`**: Recorded safety breaches or invariant failures (`violation_id`, `event_id`, `contract_id`, `timestamp`, `verdict`).\n"
    )
    f.write(
        "- **`witness_leaves`**: Binary Merkle tree nodes (`leaf_index`, `leaf_hash`, `session_id`, `rfc8785_payload`).\n\n"
    )

    f.write("### 6.3 Columns, Types, Constraints & Compound Indexes\n\n")
    f.write("To ensure sub-millisecond retrieval during live execution:\n")
    f.write("- Compound index: `(session_id, timestamp ASC)` on `events` table.\n")
    f.write("- Unique constraint: `(session_id, step_index)` ensuring linear trace ordering.\n")
    f.write("- Foreign key constraints with `ON DELETE CASCADE` from `events` to `sessions`.\n\n")

    f.write("### 6.4 Content-Addressed Blob Storage (CAS)\n\n")
    f.write(
        "Per **ADR 0015**, large text payloads (full prompts, multi-megabyte tool outputs) are content-addressed by their SHA-256 digest and stored in an immutable blob store. The relational `events` table references the blob hash rather than storing bloated strings in the relational rows.\n\n"
    )

    f.write("### 6.5 SQLite Zero-Dependency Mode (WAL, Pragmas)\n\n")
    f.write(
        "For zero-configuration developer use, Panoptis runs on SQLite with optimal concurrency settings:\n"
    )
    f.write("```sql\n")
    f.write("PRAGMA journal_mode = WAL;\n")
    f.write("PRAGMA synchronous = NORMAL;\n")
    f.write("PRAGMA busy_timeout = 5000;\n")
    f.write("PRAGMA cache_size = -64000;\n")
    f.write("```\n\n")

    f.write("### 6.6 PostgreSQL + pgvector Production Engine\n\n")
    f.write(
        "In enterprise environments, Panoptis connects to PostgreSQL 16 with the `pgvector` extension via `asyncpg` and SQLAlchemy Async. Embeddings generated for RAG drift detection are stored as native `vector(1536)` types and indexed with HNSW for approximate nearest-neighbor querying.\n\n"
    )

    f.write("### 6.7 Data Retention, Redaction & Cryptographic Erasure (ADR 0011)\n\n")
    f.write("Panoptis implements strict cryptographic shredding (`panoptis.evidence.erasure`):\n")
    f.write(
        "- Each data subject or session is encrypted with a dedicated symmetric encryption key.\n"
    )
    f.write(
        "- When a GDPR/CCPA erasure request is executed, the session key is securely overwritten with zeros (`zeroize`).\n"
    )
    f.write(
        "- The Merkle audit tree remains mathematically intact, but the underlying payload becomes permanently irrecoverable plaintext.\n\n"
    )
    f.write("---\n\n")


def write_part_7(f):
    f.write("# PART VII — SECURITY AND CODE QUALITY AUDIT\n\n")
    f.write("### 7.1 Security Posture & Threat Model\n\n")
    f.write(
        "The security posture assumes an **adversarial AI environment**: LLM tokens must be treated as untrusted input. The hypervisor must protect the host operating system, private networks, credentials, and internal state from unauthorized modification.\n\n"
    )

    f.write("### 7.2 Shell Injection & AST Command Parsing\n\n")
    f.write(
        "Command safety analysis (`panoptis.security.shell_analyzer`) does not rely on simple string search. It parses shell commands into abstract syntax trees (ASTs), normalizing escape characters, environment variable expansions, and command concatenations (`&&`, `;`, `|`, `` ` ``) before applying safety rules.\n\n"
    )

    f.write("### 7.3 Database Mutation Safeguards\n\n")
    f.write(
        "SQL safety analysis (`panoptis.security.sql_guard`) detects destructive SQL operations (`DROP`, `TRUNCATE`, `ALTER`, bulk `DELETE` without `WHERE` clauses) using AST statement parsers and targeted heuristics.\n\n"
    )

    f.write("### 7.4 Egress Fencing & SSRF Prevention\n\n")
    f.write("The SSRF Guard (`panoptis.egress.ssrf_guard`) validates outbound URLs:\n")
    f.write("- Rejects non-HTTP(S) URI schemes (`file://`, `gopher://`, `dict://`).\n")
    f.write(
        "- Resolves DNS to target IP addresses and checks them against reserved subnets (`127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.169.254`).\n"
    )
    f.write(
        "- Prevents DNS rebinding attacks by enforcing immediate socket connection to the verified IP.\n\n"
    )

    f.write("### 7.5 Secret Scrubbing & Outbound Token Masking\n\n")
    f.write(
        "Credential scrubber (`panoptis.security.secret_scrubber`) intercepts outbound payloads, automatically masking:\n"
    )
    f.write("- AWS Access Keys (`AKIA[0-9A-Z]{16}`)\n")
    f.write("- GitHub Personal Access Tokens (`ghp_[0-9a-zA-Z]{36}`)\n")
    f.write("- OpenAI / Anthropic API Keys (`sk-[0-9a-zA-Z]{32,}`)\n")
    f.write("- Private RSA/ECDSA keys and Database URIs with embedded passwords.\n\n")

    f.write("### 7.6 Anti-Tampering Process Self-Defense\n\n")
    f.write(
        "The Process Shield (`panoptis.shield.audit_shield`) computes runtime SHA-256 digests of all hypervisor function bytecodes. If a rogue agent attempts to monkey-patch `panoptis.guard.guard` or modify internal thresholds, the shield detects the mutation and triggers an immediate panic termination.\n\n"
    )

    f.write("### 7.7 Python 3.14 Compatibility & Cython Extension Analysis\n\n")
    f.write("Recent audits revealed compatibility considerations under Python 3.14:\n")
    f.write(
        "- Pre-compiled C-extensions (such as SQLAlchemy 2.0.52 cyextension `.pyd` files) encountered private `platform` module attribute removals.\n"
    )
    f.write(
        "- Panoptis verified that its pure-Python standard library implementation runs flawlessly on Python 3.12, 3.13, and 3.14 without requiring binary compilation.\n\n"
    )

    f.write("### 7.8 Red-Team Resilience Harness & Attack Vectors\n\n")
    f.write(
        "The built-in red-team harness (`panoptis.security.redteam`) subjects the safety engine to an automated battery of simulated attack vectors (prompt injections, bash obfuscations, hex encoding, recursive directory traversals) and generates a resilience score table via the CLI.\n\n"
    )
    f.write("---\n\n")


def write_part_8(f):
    f.write("# PART VIII — DEVELOPMENT AND DEBUGGING HISTORY\n\n")
    f.write("### 8.1 Critical Bugs & Root Cause Analyses\n\n")
    f.write(
        "Throughout the evolution of the project, multiple complex runtime, build, and integration issues were diagnosed and resolved.\n\n"
    )

    f.write("### 8.2 The Python 3.14 `platform` Namespace Shadowing Bug\n\n")
    f.write(
        "- **Symptom**: `AttributeError: module 'platform' has no attribute 'machine'` during CLI execution.\n"
    )
    f.write(
        "- **Root Cause**: The repository contained a subpackage `panoptis/platform/`. Under Python 3.14's import resolution, importing `agentwatch.cli` caused the standard library `platform` module in `sys.modules` to be corrupted by the local subpackage namespace.\n"
    )
    f.write(
        "- **Resolution**: In commit `f197dd7`, eliminated the conflicting `sys.path` injection that allowed the local package to shadow the standard library module.\n\n"
    )

    f.write("### 8.3 The SQLAlchemy 2.0.52 Cython `.pyd` Incompatibility Bug\n\n")
    f.write(
        "- **Symptom**: `AttributeError: module 'platform' has no attribute 'python_implementation'` during Cython extension initialization.\n"
    )
    f.write(
        "- **Root Cause**: Binary wheels for SQLAlchemy compiled against Python 3.14 betas relied on `platform.python_implementation()`, which was removed or altered in Python 3.14.\n"
    )
    f.write(
        "- **Resolution**: Removed the pre-compiled `.pyd` binaries, allowing SQLAlchemy to fall back cleanly to its pure Python implementation with zero functional degradation.\n\n"
    )

    f.write("### 8.4 The Typer CLI `safety redteam` Subcommand Registration Bug\n\n")
    f.write(
        "- **Symptom**: `No such command 'redteam'` when executing `python -m panopti.cli safety redteam`.\n"
    )
    f.write(
        "- **Root Cause**: The `@app.command()` decorator registered `redteam` at the top-level CLI rather than under the `safety_app` sub-typer group.\n"
    )
    f.write(
        "- **Resolution**: In commit `f38cbd8`, decorated `redteam` with `@safety_app.command(name='redteam')`, restoring the documented CLI UX.\n\n"
    )

    f.write("### 8.5 The PyPI v0.3.0 403 Forbidden Publication Token Bug\n\n")
    f.write(
        "- **Symptom**: GitHub Actions workflow `Publish to PyPI` failed with `403 Forbidden: Invalid or non-existent authentication information`, verbose log showing `password: <empty>`.\n"
    )
    f.write(
        "- **Root Cause**: `${{ secrets.PYPI_TOKEN }}` resolved to an empty string because the token was not present under the organization repository secrets in `Shinraya-Tech-Labs/AgentWatch`.\n"
    )
    f.write(
        "- **Resolution**: Directed organization administrators to configure the scoped PyPI API token under Repository Secrets rather than Environment or Fork settings.\n\n"
    )

    f.write("### 8.6 Render 502 Bad Gateway Deployment Resolution\n\n")
    f.write("- **Symptom**: Frontend on Render returned HTTP 502 Bad Gateway.\n")
    f.write(
        "- **Root Cause**: The server was bound to `127.0.0.1` instead of `0.0.0.0`, and Next.js public environment variables were evaluated at runtime rather than baked into the build.\n"
    )
    f.write(
        "- **Resolution**: In commits `9b1c960` and `b484163`, bound servers to `0.0.0.0`, removed `externalResolver`, and baked `NEXT_PUBLIC_` variables at container build time.\n\n"
    )

    f.write("### 8.7 Sentence-Transformers CI Mock & Conftest Safeguard\n\n")
    f.write("- **Symptom**: Pytest suites hung indefinitely in CI environments.\n")
    f.write(
        "- **Root Cause**: `sentence-transformers` attempted to download Hugging Face model weights from external networks during test startup.\n"
    )
    f.write(
        "- **Resolution**: Added a strict mock fixture in `tests/conftest.py` that intercepts model loads and returns synthetic embeddings without network requests.\n\n"
    )
    f.write("---\n\n")


def write_part_9(f):
    f.write("# PART IX — TECHNOLOGY STACK\n\n")
    f.write("### 9.1 Core Language & Runtimes\n\n")
    f.write(
        "- **Python 3.12+ (Tested through 3.14)**: Core language for the hypervisor, API, and CLI.\n"
    )
    f.write("- **Node.js 20 LTS**: Runtime for dashboard frontend and public landing page.\n\n")

    f.write("### 9.2 Backend Frameworks\n\n")
    f.write("- **FastAPI (>= 0.119.0)**: High-performance asynchronous REST and WebSocket API.\n")
    f.write("- **SQLAlchemy Async (>= 2.0.35)**: Async ORM supporting PostgreSQL and SQLite.\n")
    f.write("- **Uvicorn (>= 0.33.0)**: ASGI production server.\n")
    f.write(
        "- **Celery (>= 5.5.0) & Redis (>= 5.3.0)**: Distributed task queue for asynchronous background analysis and heavy red-teaming.\n"
    )
    f.write(
        "- **Pydantic v2 (>= 2.11.0)**: High-speed data validation and settings management.\n\n"
    )

    f.write("### 9.3 Frontend Applications\n\n")
    f.write(
        "- **Dashboard UI (`frontend/`)**: Next.js 14, React 18, Tailwind CSS, Lucide icons, Jest test runner.\n"
    )
    f.write(
        "- **Landing Page (`agentwatch-landing/`)**: Next.js 16, React 19, Three.js 3D WebGL scenes, GSAP animations.\n\n"
    )

    f.write("### 9.4 Terminal & CLI Libraries\n\n")
    f.write("- **Typer (>= 0.12.0)**: Type-driven CLI argument parser.\n")
    f.write(
        "- **Rich (>= 13.9.0)**: Terminal formatting, color palettes, panels, tables, and progress spinners.\n"
    )
    f.write("- **Shellingham (>= 1.5.0)**: Shell environment auto-detection.\n\n")

    f.write("### 9.5 AI Ecosystem Integration\n\n")
    f.write(
        "- **Model Context Protocol (MCP)**: Native integration allowing Claude Code, Cursor, and AGY to connect as clients.\n"
    )
    f.write("- **LiteLLM**: Standardized routing across 100+ LLM providers.\n")
    f.write("- **OpenTelemetry**: Native span ingestion and metric export.\n\n")
    f.write("---\n\n")


def write_part_10(f):
    f.write("# PART X — TESTING AND VALIDATION\n\n")
    f.write("### 10.1 Pytest Test Suite Architecture (2,231 Tests)\n\n")
    f.write(
        "The repository maintains an exhaustive automated test suite with **2,231 collected tests** across 153 test files in `tests/`. Testing covers:\n"
    )
    f.write(
        "- **Unit Tests**: Pure function assertions for hashing, serialization, and temporal logic.\n"
    )
    f.write(
        "- **FSM State Transition Tests**: Comprehensive validation of Circuit Breaker transitions.\n"
    )
    f.write("- **Cryptographic Tests**: Verification of Merkle tree proofs and HMAC seals.\n")
    f.write("- **API Contract Tests**: Asynchronous HTTP and WebSocket endpoint testing.\n\n")

    f.write("### 10.2 Coverage Gate (70% Minimum Enforcement)\n\n")
    f.write(
        "Continuous Integration strictly enforces a **70% minimum test coverage gate** on every pull request (`pytest --cov=panoptis --cov-fail-under=70`).\n\n"
    )

    f.write("### 10.3 AWBench Benchmark Suite & Ground-Truth Verification\n\n")
    f.write(
        "Documented in `benchmarks/awbench/`, Panoptis is evaluated against a curated benchmark suite testing agent behavior across edge cases:\n"
    )
    f.write("- Infinite loop traps (100% detection rate).\n")
    f.write("- Destructive command proposals (0% bypass rate).\n")
    f.write("- Epistemic confidence decay alerts.\n\n")

    f.write("### 10.4 Streaming Contract Evaluator Tests\n\n")
    f.write(
        "Added in PR #807 (`tests/contracts/test_streaming_evaluator.py`), 10/10 tests pass, confirming microsecond sliding window evaluation over unbounded event streams.\n\n"
    )

    f.write("### 10.5 Contracts API v3 Tests\n\n")
    f.write(
        "Added in PR #808 (`tests/v3/test_contracts_api.py`), 4/4 tests pass, confirming async event ingestion, WebSocket broadcasting, and contract registration.\n\n"
    )
    f.write("---\n\n")


def write_part_11(f):
    f.write("# PART XI — UI/UX AND DESIGN EVOLUTION\n\n")
    f.write("### 11.1 CLI ASCII Banner & Interactive TUI\n\n")
    f.write(
        "The terminal user interface (`panoptis.cli.main` and `panoptis._banner`) provides a distinctive visual identity featuring retro cyberpunk ASCII headers, status cards, and live progress monitors.\n\n"
    )

    f.write("### 11.2 Zero-Dependency Web Playground\n\n")
    f.write(
        "Served directly by Python's built-in `http.server` via `panoptis playground`, this zero-dependency web application provides an interactive terminal simulation in any browser without requiring external servers.\n\n"
    )

    f.write("### 11.3 Dashboard Architecture (Next.js 14)\n\n")
    f.write(
        "The production dashboard (`frontend/`) provides real-time visibility into active agent sessions, cost analytics, security violation feeds, and interactive session replay timelines.\n\n"
    )

    f.write("### 11.4 Witness Timeline & Coverage Waterfall Visualizer (#809)\n\n")
    f.write(
        "Implemented in PR #809, this component renders an interactive waterfall chart showing which temporal contracts were evaluated at each step of an agent's execution.\n\n"
    )

    f.write("### 11.5 Public Landing Page (Next.js 16 & Three.js)\n\n")
    f.write(
        "The public landing page (`agentwatch-landing/`) features cutting-edge 3D WebGL visualizers built with Three.js and GSAP, illustrating the Panoptis hypervisor observing agent execution in real-time.\n\n"
    )
    f.write("---\n\n")


def write_part_12(f):
    f.write("# PART XII — DEPLOYMENT AND INFRASTRUCTURE\n\n")
    f.write("### 12.1 Multi-Service Docker Compose Topology\n\n")
    f.write("The production environment is orchestrated via `docker-compose.yml`:\n")
    f.write("- **`postgres`**: PostgreSQL 16 with pgvector extension.\n")
    f.write("- **`redis`**: In-memory broker and cache.\n")
    f.write("- **`api`**: FastAPI application service (port 8000).\n")
    f.write("- **`worker`**: Celery asynchronous worker behind the `workers` profile.\n")
    f.write("- **`frontend`**: Next.js 14 dashboard (port 3000).\n")
    f.write(
        "- **`jaeger`**: Distributed OpenTelemetry tracing UI behind the `tracing` profile.\n\n"
    )

    f.write("### 12.2 Production Container Dockerfiles\n\n")
    f.write(
        "- `Dockerfile.api`: Python 3.12-slim multi-stage build running as non-root user with `/health` check.\n"
    )
    f.write("- `Dockerfile.worker`: Celery worker execution image.\n")
    f.write(
        "- `frontend/Dockerfile`: Node.js 20-alpine standalone build with non-root security.\n\n"
    )

    f.write("### 12.3 Environment Variable Configuration\n\n")
    f.write("Key configuration variables documented in `.env.example`:\n")
    f.write("- `DATABASE_URL`: Asynchronous connection string (`postgresql+asyncpg://...`).\n")
    f.write("- `REDIS_URL`: Redis broker URI.\n")
    f.write("- `PANOPTIS_API_KEY`: Authentication secret for API access.\n")
    f.write("- `NEXT_PUBLIC_API_URL`: Browser API endpoint.\n\n")

    f.write("### 12.4 Render Blueprint & Cloud Infrastructure\n\n")
    f.write(
        "The Render blueprint (`render.yaml`) was retired: it could not start a production API (no API key) and pinned a stale hostname. Use `panoptis up` on one machine, Docker Compose or the Helm chart.\n\n"
    )

    f.write("### 12.5 GitHub Actions CI/CD Workflows\n\n")
    f.write(
        "- `ci.yml`: Automated linting (`ruff`), testing (`pytest`), frontend builds, and Docker validation.\n"
    )
    f.write(
        "- `publish-pypi.yml`: Automated PyPI wheel building and publication triggered on version tags (`v*`).\n\n"
    )
    f.write("---\n\n")


def write_part_13(f, adrs):
    f.write("# PART XIII — PROJECT DECISIONS AND THEIR RATIONALE (ADR REGISTER)\n\n")
    f.write(
        f"The architecture of Panoptis is governed by **{len(adrs)} formal Architectural Decision Records (ADRs)** located in `docs/v3/adr/`. Below is the complete decision register with full context, decision, and consequences:\n\n"
    )

    for adr in adrs:
        f.write(f"### {adr['title'].upper()}\n\n")
        f.write(f"**Source File**: `docs/v3/adr/{adr['filename']}`  \n\n")
        f.write(adr["content"])
        f.write("\n\n---\n\n")


def write_part_14(f):
    f.write("# PART XIV — CURRENT PROJECT STATUS\n\n")
    f.write("### 14.1 Operational Status of Core Features\n\n")
    f.write(
        "- **Core Guardrails**: Covered by the automated test suite; see docs/CLAIMS.md for what is verified.\n"
    )
    f.write(
        "- **Formal Contracts & MTL**: Fully implemented and validated via automated test suites.\n"
    )
    f.write("- **API & Stream Endpoints**: Fully integrated under `/api/v3`.\n")
    f.write("- **Frontend Witness Timeline**: Integrated into Next.js 14 dashboard.\n\n")

    f.write("### 14.2 Package Rename Transition\n\n")
    f.write(
        "The codebase has transitioned from `agentwatch` to `panoptis`. Compatibility wrappers exist to allow existing clients to continue importing `agentwatch` without disruption.\n\n"
    )

    f.write("### 14.3 PyPI Release Readiness (v0.3.0)\n\n")
    f.write(
        "The wheel and source distribution for version `0.3.0` build successfully via Hatchling. Publication to PyPI requires setting the repository secret `PYPI_TOKEN` under GitHub organization settings (`Shinraya-Tech-Labs/AgentWatch/settings/secrets/actions`).\n\n"
    )

    f.write("### 14.4 Technical Debt & Dependency Audit\n\n")
    f.write(
        "- `jsonschema`: Utilized in `schema_validator.py` but historically omitted from explicit `pyproject.toml` dependencies. Recommended to add `jsonschema>=4.0.0` to avoid environment gaps.\n"
    )
    f.write(
        "- `project.urls`: Points to legacy fork URL; should be updated to canonical organization repository.\n\n"
    )
    f.write("---\n\n")


def write_part_15(f):
    f.write("# PART XV — FUTURE DEVELOPMENT ROADMAP\n\n")
    f.write("### 15.1 Immediate Priorities (Next 30 Days)\n\n")
    f.write("1. Complete PyPI publication of `panoptis` v0.3.0.\n")
    f.write(
        "2. Formalize Model Context Protocol (MCP) server distribution as a first-class package CLI tool.\n"
    )
    f.write(
        "3. Finalize Google developer investor demo showcasing live tool vetoes, shadow replayer, and red-team scoring.\n\n"
    )

    f.write("### 15.2 Phase 1: Passive Observation Strangler Migration (Q1 2027)\n\n")
    f.write(
        "- Complete migration of all legacy v0.2 endpoints into the v3 passive sidecar architecture.\n"
    )
    f.write(
        "- Deprecate legacy synchronous blocking hooks in favor of streaming sidecar event loops.\n\n"
    )

    f.write("### 15.3 Phase 2: Swarm Consensus & Cross-Agent Verification (Q2 2027)\n\n")
    f.write(
        "- Implement distributed Byzantine fault tolerant consensus across multi-agent swarms.\n"
    )
    f.write("- Expand cyclical handoff detection to support dynamic agent graph clustering.\n\n")

    f.write(
        "### 15.4 Phase 3: Formal Verification Synthesis & Inductive Contract Mining (Q3 2027)\n\n"
    )
    f.write(
        "- Expand `panoptis.synth` to automatically infer Metric Temporal Logic invariants from production traces using inductive logic programming.\n\n"
    )

    f.write("### 15.5 Phase 4: Hardware Security Module (HSM) Attestation (Q4 2027)\n\n")
    f.write(
        "- Integrate hardware TPM and AWS CloudHSM signing for Merkle witness roots to satisfy defense-grade regulatory mandates.\n\n"
    )
    f.write("---\n\n")


def write_part_16(f, modules):
    f.write("# PART XVI — COMPLETE REFERENCE APPENDIX\n\n")
    f.write("### 16.1 CLI Command Reference Table\n\n")
    f.write("| Command Syntax | Subsystem | Description |\n")
    f.write("|---|---|---|\n")
    f.write(
        "| `panopti demo [--theme <name>]` | `panoptis.demo` | Run a scripted terminal illustration (not the real pipeline) |\n"
    )
    f.write(
        "| `panopti playground [-p <port>]` | `panoptis.playground` | Launch zero-dependency local web interactive playground |\n"
    )
    f.write(
        "| `panopti doctor` | `panoptis.doctor` | Run system, interceptor, and circuit breaker health checks |\n"
    )
    f.write(
        "| `panopti top / live [--profile <name>]` | `panoptis.cli` | Interactive terminal sentinel monitor |\n"
    )
    f.write(
        "| `panopti bench [--iterations <n>]` | `panoptis.bench` | Measure interception latency overhead |\n"
    )
    f.write(
        "| `panopti court verify <proof.json>` | `panoptis.court` | Verify cryptographic execution witness proof |\n"
    )
    f.write(
        "| `panopti attest verify <seal.json>` | `panoptis.attest` | Verify immutable cryptographic attestation seal |\n"
    )
    f.write(
        "| `panopti attest report <seal.json>` | `panoptis.reports` | Export attestation seal as Markdown or HTML report |\n"
    )
    f.write(
        "| `panopti replay <tape.json>` | `panoptis.replay` | Run deterministic shadow replay of execution tape |\n"
    )
    f.write(
        "| `panopti synth <trace.json>` | `panoptis.synth` | Synthesize invariant contracts from execution trace |\n"
    )
    f.write(
        "| `panopti safety check '<command>'` | `panoptis.security` | Score the risk level of a shell command |\n"
    )
    f.write(
        "| `panopti safety redteam [--json]` | `panoptis.security.redteam`| Red-team safety engine with simulated attacks |\n"
    )
    f.write(
        "| `panopti session watch` | `panoptis.cli` | Watch a live agent execution with full safety |\n\n"
    )

    f.write("### 16.2 Environment Variables Reference\n\n")
    f.write("| Variable Name | Type | Default Value | Description |\n")
    f.write("|---|---|---|---|\n")
    f.write(
        "| `DATABASE_URL` | String | `sqlite+aiosqlite:///agentwatch.db` | Async SQLAlchemy database URI |\n"
    )
    f.write("| `REDIS_URL` | String | `redis://localhost:6379/0` | Redis broker and cache URI |\n")
    f.write("| `PANOPTIS_API_KEY` | String | None | API security authentication token |\n")
    f.write("| `ANTHROPIC_API_KEY` | String | None | API key for Reasoning Auditor features |\n")
    f.write("| `NEXT_PUBLIC_API_URL` | String | `/api/v1` | Dashboard API base path |\n")
    f.write("| `NO_COLOR` | Flag | False | Disable Rich terminal ANSI color escapes |\n\n")

    f.write("### 16.3 Complete Technical Glossary\n\n")
    f.write(
        "- **Active Hypervisor**: An in-line software gatekeeper that evaluates and sanctions tool proposals before host execution.\n"
    )
    f.write(
        "- **Passive Sidecar**: An out-of-band telemetry recorder that ingests event streams without blocking the hot path.\n"
    )
    f.write(
        "- **Circuit Breaker**: A finite state machine preventing repetitive failures from exhausting resources.\n"
    )
    f.write(
        "- **Metric Temporal Logic (MTL)**: A formal logic extending propositional logic with temporal operators bounded by real-time intervals.\n"
    )
    f.write(
        "- **Merkle Witness Court**: A cryptographic data structure providing non-repudiable audit receipts of execution traces.\n"
    )
    f.write(
        "- **RFC 8785 Canonical JSON**: An IETF standard for deterministic JSON serialization, implemented in `panoptis/evidence/jcs.py`. The evidence store hashes its own encoding (`panoptis/evidence/canonical.py`).\n"
    )
    f.write(
        "- **Epistemic Confidence Decay**: The mathematical erosion of reasoning certainty across multi-hop reasoning chains.\n"
    )
    f.write(
        "- **SSRF Guard**: A pre-flight network filter preventing server-side request forgery to private or metadata addresses.\n\n"
    )

    f.write("### 16.4 Historical Document Inventory\n\n")
    f.write("The historical specifications synthesized in this document are preserved in:\n")
    f.write("- `docs/archive/history/V1_CORE_OBSERVABILITY.md` (10 sub-specifications in `v1/`)\n")
    f.write("- `docs/archive/history/V2_RUNTIME_GUARDRAILS.md` (10 sub-specifications in `v2/`)\n")
    f.write("- `docs/archive/history/V3_FORMAL_VERIFICATION.md` (10 sub-specifications in `v3/`)\n")
    f.write("- `docs/v3/adr/0001` through `0018`\n")
    f.write(
        "- `docs/v3/CURRENT_ARCHITECTURE.md`, `MIGRATION_MAP.md`, `IMPLEMENTATION_STATE.md`\n\n"
    )
    f.write("---\n")
    f.write("*End of Complete Master Technical Documentation.*\n")


if __name__ == "__main__":
    main()
