"""A small multi-component research system instrumented with AgentWatch v3.

It runs offline and deterministically (the "model" is a local extractive stub), so its
behaviour can be reconstructed, compared and replayed exactly.

    panoptis observe python examples/research_system.py
    panoptis observe python examples/research_system.py --variant flaky_tool
    panoptis inspect latest

Variants change behaviour in controlled ways:
    normal        default pipeline
    flaky_tool    the calculator fails twice before succeeding (retry loop)
    bad_retrieval the retriever returns an off-topic document
    model_v2      a different summarizer model with longer outputs
    stale_memory  the writer reads a stale note from memory
"""

from __future__ import annotations

import argparse
import re

from panoptis import instrument as aw

CORPUS = {
    "D1": "Photosynthesis converts light energy into chemical energy stored in glucose molecules inside plant chloroplasts.",
    "D2": "Solar panels convert sunlight into electricity using photovoltaic cells made of semiconductor materials such as silicon.",
    "D3": "The efficiency of commercial silicon photovoltaic cells typically ranges between eighteen and twenty two percent.",
    "D4": "Wind turbines generate electricity when moving air rotates blades connected to a generator.",
    "D5": "Medieval castles were built with thick stone walls and moats to defend against attackers.",
    "D6": "Perovskite solar cells are an emerging photovoltaic technology with rapidly improving laboratory efficiency.",
    "D7": "Grid scale batteries store surplus solar electricity so it can be used after sunset.",
}

VARIANT = {"name": "normal"}
MEMORY: dict[str, str] = {"notes:solar": "Old note: solar panels are mostly used on satellites."}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3}


@aw.retriever("corpus-index", actor="agent:researcher")
def retrieve(query: str, k: int = 3) -> list[dict[str, str]]:
    if VARIANT["name"] == "bad_retrieval":
        return [{"id": "D5", "text": CORPUS["D5"]}, {"id": "D4", "text": CORPUS["D4"]}]
    q = _words(query)
    ranked = sorted(CORPUS.items(), key=lambda kv: (-len(q & _words(kv[1])), kv[0]))
    return [{"id": doc_id, "text": text} for doc_id, text in ranked[:k]]


@aw.model("local/extractive-summarizer-v1", actor="agent:researcher")
def summarize(prompt: str) -> str:
    sentences = [s.strip() for s in prompt.split("\n") if s.strip().startswith("-")]
    return "Findings: " + " ".join(s.lstrip("- ") for s in sentences[:2])


@aw.model("local/extractive-summarizer-v2", actor="agent:researcher")
def summarize_v2(prompt: str) -> str:
    sentences = [s.strip() for s in prompt.split("\n") if s.strip().startswith("-")]
    return (
        "Detailed findings: "
        + " ".join(s.lstrip("- ") for s in sentences)
        + " Further research is recommended."
    )


_calc_failures = {"remaining": 0}


@aw.tool("calculator", actor="agent:analyst")
def calculator(expression: str) -> float:
    if _calc_failures["remaining"] > 0:
        _calc_failures["remaining"] -= 1
        raise TimeoutError("calculator backend timed out")
    a, op, b = expression.split()
    return {
        "+": float(a) + float(b),
        "-": float(a) - float(b),
        "*": float(a) * float(b),
        "/": float(a) / float(b),
    }[op]


def research(topic: str, variant: str) -> str:
    with aw.span("OPERATION", "plan", actor="agent:planner") as plan:
        plan.input(topic, role="request")
        aw.delegate("agent:planner", "agent:researcher", {"task": f"research {topic}"})
        aw.delegate("agent:planner", "agent:analyst", {"task": "estimate yearly output"})

        with aw.span("OPERATION", "research", actor="agent:researcher") as research_span:
            docs = retrieve(topic)
            prompt = "Summarize the key facts:\n" + "\n".join(f"- {d['text']}" for d in docs)
            model = summarize_v2 if variant == "model_v2" else summarize
            findings = model(prompt)
            aw.memory_write("notes", "notes:solar", findings, actor="agent:researcher")
            MEMORY["notes:solar"] = findings

        with aw.span("OPERATION", "analysis", actor="agent:analyst") as analysis_span:
            if variant == "flaky_tool":
                _calc_failures["remaining"] = 2
            value = None
            for _attempt in range(4):
                try:
                    value = calculator("5 * 365")
                    break
                except TimeoutError:
                    continue
            estimate = (
                f"A 5 kWh/day array produces about {value:.0f} kWh per year."
                if value
                else "Estimate unavailable."
            )

        with aw.span(
            "OPERATION", "write", actor="agent:writer", links=[research_span, analysis_span]
        ) as write:
            note = aw.memory_read(
                "notes",
                "notes:solar",
                MEMORY["notes:solar"]
                if variant != "stale_memory"
                else "Old note: solar panels are mostly used on satellites.",
                actor="agent:writer",
            )
            aw.message("agent:writer", "agent:reviewer", {"draft": note[:80]})
            report = f"# {topic.title()}\n\n{note}\n\n{estimate}\n"
            write.output(report, role="draft")
            aw.artifact("report.md", report, actor="agent:writer")
        return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--variant",
        default="normal",
        choices=["normal", "flaky_tool", "bad_retrieval", "model_v2", "stale_memory"],
    )
    parser.add_argument("--topic", default="solar photovoltaic electricity")
    args = parser.parse_args()
    VARIANT["name"] = args.variant
    with aw.run(
        "research_system",
        variant=args.variant,
        topic=args.topic,
        system="research_system",
        code_version="1.0",
    ) as run:
        report = research(args.topic, args.variant)
        if hasattr(run, "set_outcome"):
            run.set_outcome("ok")
    print(report)


if __name__ == "__main__":
    main()
