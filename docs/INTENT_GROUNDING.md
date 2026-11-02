# Intent grounding

Some tool calls are well-formed and in policy but still wrong for the task. `DELETE FROM orders WHERE customer_id = 123` is a scoped statement, and it is still wrong when the user asked only to *look up* customer 123. The intent stage (`Stage.INTENT`) compares each call with the user's stated objective, before the call runs.

Code: `panoptis/protect/intent.py` (`IntentGrounder`, `GroundingConfig`, `extract_objective`). Tests: `tests/protect/test_intent_grounding.py`.

## The objective and authorized context

The objective comes from one of these, in order:

1. `ToolAction.objective`, for each call;
2. the session's objective: `pipeline.start_session(agent, session, objective=...)`, changed with `set_objective(...)`;
3. the Claude Code hook's `AGENTWATCH_OBJECTIVE`.

Both `start_session` and `set_objective` are recorded in the tenant's ledger. Calling `set_objective` on a session that was never started is an error, so an objective is never silently dropped.

**Authorized context** is what the integration knows besides the text. It is passed as `start_session(metadata={"authorized_context": {...}})` and may contain `hosts`, `paths`, `tables` and `entities` (`{kind: [values]}`). It widens what the task names, so a destination or a path in the context counts as requested. It never changes the task's mode: a read-only task stays read-only.

From the text the grounder extracts:

- **The mode.** `read_only` (find, check, summarise, a question, …), `mutating` (fix, update, post, …), `destructive` (delete, drop, clean up, …) or `unknown`. The mode comes from the imperative verb that opens each clause, so "look up X, then delete Y" is destructive.
- **Explicit prohibitions:** "don't delete", "read-only", "without modifying", "remove nothing", "leave everything as is", "just look".
- **Named entities:** `customer 123`, `order #456`, paths, hosts and tables.

## Findings

| Code | When | Recommends |
|---|---|---|
| `GOAL_ACTION_MISMATCH` | read-only task, destructive call | BLOCK |
| `GOAL_ACTION_MISMATCH` | read-only task, mutating call | ESCALATE |
| `OBJECTIVE_PROHIBITS` | the task forbids what the call does | BLOCK |
| `DESTRUCTION_NOT_REQUESTED` | mutating task, destructive call | ESCALATE |
| `AMBIGUOUS_OBJECTIVE` | the mode cannot be determined and the call is high-risk (destroys, sends data, reads secrets) | `ambiguous_high_risk` (ESCALATE) |
| `TARGET_NOT_REQUESTED` | a destructive call on tables or paths outside those the task names | ESCALATE |
| `ENTITY_MISMATCH` | same identifier kind, different value (`customer 123` vs `customer_id = 999`) | ESCALATE on writes; reported on reads |
| `UNREQUESTED_DESTINATION` | data sent to a host the task never names, while it names others | ESCALATE |
| `LOW_GOAL_OVERLAP` | the call shares little vocabulary with the task | ESCALATE only for a high-risk call under a read-only or unknown task; otherwise reported |
| `NO_OBJECTIVE` | no objective | `missing_objective_high_risk` for high-risk calls (default ALLOW: grounding is opt-in) |
| `GROUNDED` | none of the above | ALLOW |

Every intent finding records a rationale (the message), the objective's mode, the evidence (requested vs acted-on entities, targets, destinations, overlap) and a `confidence` between 0 and 1. All of these go into the decision in the evidence ledger.

The confidence says how sure the heuristic is of the finding:

- 0.9 for a prohibition;
- 0.85 for a mode mismatch;
- about 0.3 for low overlap.

For `GROUNDED`, it is higher when the mode is known and the call shares the task's words. It explains a decision; it never makes one.

## Similarity is evidence, never authorization

Token overlap between the task and the call is reported, but it can only *raise* a high-risk call to review. It never allows one:

- **Repeating the task's words does not help.** A destructive call that repeats the task's words is still blocked under a read-only task: `DROP TABLE orders -- check orders table customer 123` under "Check the orders table for customer 123".
- **Ambiguity escalates regardless of overlap.** Under an ambiguous objective ("orders table"), a destructive call escalates however much it overlaps.
- **The agent cannot change the objective.** Its reasoning (`ToolAction.reasoning`) does not change the objective, and neither do read-only words placed inside a command.
- **Permission in a subordinate clause is not taken.** "it's fine to delete them afterwards", "don't hesitate to delete" and "check whether we can delete" do not turn a read task into a destructive one. These are deliberately conservative: such tasks need an imperative ("delete the old logs").

## Optional model verifier

`GroundingConfig(verifier=...)` takes a callable `(objective, tool, arguments) -> (answer, reason)`. `openai_compatible_verifier(...)` is one for any local OpenAI-compatible endpoint. The verifier is a **secondary** signal:

- **It is consulted only for high-risk calls**, unless `verify_only_high_risk=False`.
- **It can only add restrictions.**
  - `NO` → `VERIFIER_REJECTED`, recommending `verifier_no_verdict` (BLOCK).
  - `YES` → `VERIFIER_CONFIRMED`, which is ALLOW and never overrides a heuristic block or escalation.
  - Anything else → `VERIFIER_UNSURE`.
- **It is bounded in time.** A verifier slower than `verifier_timeout_s` (5 s) or one that raises is reported as `VERIFIER_UNAVAILABLE`, and the heuristic result stands. Reasons are truncated to 500 characters.

## Limitations

- **The heuristics are English-only.** The verb lists and patterns cover common English phrasings of tasks. A task in another language parses as `unknown`, so high-risk calls escalate.
- **Mode detection reads the opening verb of each clause.** Unusual phrasing can therefore land in `unknown`. That is the safe direction (escalation), but it can cost an approval.
- **Path and table matching is lexical.** It does not resolve symlinks or a database's search path.
- **Grounding is a second opinion.** It does not replace RBAC or threat analysis: an aligned call can still be blocked by those stages.
