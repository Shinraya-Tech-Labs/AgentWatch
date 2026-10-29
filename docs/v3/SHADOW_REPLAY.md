# Shadow replay

`agentwatch.replay.shadow` answers one debugging question: **did this agent fail because the model
wrote something unexpected, or would it fail again if shown the same tool results?**

It replays a recorded session against a model while every tool call is answered from the
**recorded evidence**. Nothing is executed, so replaying cannot touch a database, an API or the file
system, and no HTTP mock server is needed.

```python
from agentwatch.replay import ShadowConfig, run_shadow_replay

report = run_shadow_replay(recorded_events, model, ShadowConfig(temperature=0.0, seed=42))
print(report.classification, report.summary)
```

## How a replay works

1. A read-only `ToolEvidenceStore` pairs each recorded tool call with its result (by `tool_id` when
   present, otherwise the oldest unanswered call to the same tool) and indexes it by
   **tool name + canonical arguments**. Key order in the arguments never matters.
2. The model is asked for each decision the original run made. It receives a `ShadowContext`: the shadow
   history so far, a copy of the recorded decision, and the `ShadowConfig` (temperature 0 and an optional
   seed by default).
3. When the model makes a tool call, the store answers with the next recorded result for that exact call.
   The same call made twice gets the first and then the second recorded answer, in order, which keeps a
   flaky tool (503 then 200) faithful.
4. A call with no recorded answer is **missing evidence**. Replay stops there, because everything after it
   would depend on a made-up value.
5. The shadow trace is compared with the historical one position by position.

Everything handed to the model or served as evidence is a deep copy, so no replay can change the
recorded session or poison a later run.

## What is compared

Each event is reduced to the facts that matter: event type, tool name, a hash of the arguments, a hash of
the result (output, error, exit code), status and a hash of the planner or message text. Event ids,
timestamps and durations are ignored.

## Classification

| Class | Meaning |
| --- | --- |
| `IDENTICAL_REPLAY` | The shadow trace matches exactly and the recorded run did not fail |
| `DETERMINISTIC_CORROBORATION` | The shadow trace matches exactly and the recorded run **failed**: given the recorded tool states, the failure reproduces, so it is not model noise |
| `STOCHASTIC_MODEL_DIVERGENCE` | The model took a different branch although every tool result it saw matched the recording. If the recorded run failed, the failure did not reproduce |
| `INSUFFICIENT_EVIDENCE` | The replay could not be completed faithfully: a call had no recorded answer, or a result position was not served from the recording. No conclusion about the cause is drawn |

The report also carries the first divergence index and each divergence's changed fields, the missing
evidence (with how many differently-parameterised recorded calls exist for the same tool), how much evidence
was served and left unused, and the normalized tool-sequence distance from `replay.divergence`.

## Plugging in a model

* A `ShadowModel` is `Callable[[ShadowContext], AgentEvent | None]`. Return `None` to stop.
* `from_step_fn(step_fn)` adapts an existing counterfactual `StepFn`.
* `PromptCompletionModel(complete)` re-sends each recorded prompt through `complete(prompt, config)` and
  compares the completion. Decisions without a prompt are repeated as recorded, so only the model stage
  varies. For agents whose tool choices come from the model, pass a custom `ShadowModel`.

The model may make up to `max_extra_decisions` (default 3) decisions after the recording ended, so a shadow
that keeps going is reported as a divergence instead of being cut off. A terminal event
(`AGENT_END`, `SESSION_END`, `TASK_COMPLETE`, ...) ends the replay.

## Limits

* Evidence is matched on tool name and exact arguments. A model that rewords an argument is reported as
  missing evidence rather than guessed at.
* This isolates **model** versus **recorded tool state**. It does not call the live tool, so it cannot tell
  whether the tool would answer differently today.
* Results are only as complete as the recording; calls whose results were never captured are
  `INSUFFICIENT_EVIDENCE`.
