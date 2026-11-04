# Getting Started with AgentWatch

AgentWatch gives you a fast path from "clone the repo" to "watching your first agent session in the dashboard." This guide is written for a clean machine and uses the smallest working setup that matches the code in this repository.

## Before You Start

You will need:

- Python 3.12 or newer
- Docker Desktop or Docker Engine with the Docker Compose plugin
- A terminal open at the repository root

If you are on Windows, make sure Docker Desktop is running before you start the stack.

## 1. Installation

Install the Python package first:

```bash
python -m pip install panoptis
```

Start the local stack:

```bash
docker compose up -d
```

What this starts:

| Service | Port | Purpose |
| --- | --- | --- |
| PostgreSQL | 5432 | Stores sessions, events, checkpoints, and dashboard data |
| Redis | 6379 | Used by the background worker and queue |
| API | 8000 | REST API, WebSocket stream, and alerting hooks |
| Frontend | 3000 | The dashboard UI |

Optional tracing services are available too:

```bash
docker compose --profile tracing up -d
```

That adds Jaeger on:

- 16686 for the Jaeger UI
- 4317 and 4318 for OTLP ingest

### Environment Variables

For a local Docker setup, the compose file already injects the core backend values. If you need to run the API or frontend outside Docker, use these defaults:

```bash
DATABASE_URL=postgresql+asyncpg://panoptis:panoptis@localhost:5432/panoptis
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
AGENTWATCH_ENV=development
AGENTWATCH_API_URL=http://localhost:8000
NEXT_PUBLIC_API_URL=/api/v1
```

#### `AGENTWATCH_API_URL` vs `NEXT_PUBLIC_API_URL`

These two look interchangeable and are not. They are read by different halves of the dashboard, at
different times, and setting the wrong one produces a dashboard that builds cleanly, starts cleanly,
and talks to the wrong host — with no error to tell you so.

```
browser  ──( NEXT_PUBLIC_API_URL = /api/v1 )──▶  Next.js app
                                                     │
                                    pages/api/v1/[...path].ts   (reverse proxy)
                                                     │
                         ──( AGENTWATCH_API_URL = http://api:8000 )──▶  FastAPI
```

| | `NEXT_PUBLIC_API_URL` | `AGENTWATCH_API_URL` |
|---|---|---|
| read by | the **browser** (`frontend/lib/api/config.ts`) | the **server** (`frontend/pages/api/v1/[...path].ts`) |
| read when | **build time** — inlined into the JS bundle | **request time** — from the live container env |
| value | a **relative path** (`/api/v1`) | an **absolute URL** to the API |
| to change it | **rebuild** the image | **restart** the container |

**Why two.** The browser cannot resolve `http://api:8000` — `api` is a Docker-network hostname that
only means anything inside the Compose network. So the browser is pointed at a relative path on the
Next.js app itself, and a server-side route proxies that through to the API using the internal
hostname. The browser never talks to the API directly.

**The failure mode this prevents.** If the dashboard can't reach the API, the tempting fix is to point
`NEXT_PUBLIC_API_URL` at the API directly — `http://api:8000`, or `http://localhost:8000`. That makes
it worse: the browser then tries to resolve a hostname it cannot see, and requests fail from the
client with nothing useful in the server logs. And because `NEXT_PUBLIC_*` is baked in at build time,
editing it in `docker-compose.yml` has **no effect at all** until the image is rebuilt — so a
correct-looking change can appear to do nothing, which sends people looking in the wrong place.

If the dashboard can't reach the API, `AGENTWATCH_API_URL` is almost always the one you want.

**Why the proxy exists at all.** An earlier version used a `next.config.js` rewrite. Rewrites are
evaluated at build time, so `AGENTWATCH_API_URL` was baked into `routes-manifest.json` as
`http://localhost:8000` — the env var isn't present during `docker build` — and the container then
ignored whatever Compose set at runtime. The API route replaced it precisely so the value is read
from the live environment on every request. That's also why it must stay an API route rather than
going back to being a rewrite.

### Docker Requirements

- Docker must be able to run Linux containers
- Compose must support the `docker compose` command
- The stack expects ports 3000, 5432, 6379, and 8000 to be free

If one of those ports is already in use, stop the conflicting process before starting AgentWatch.

## 2. Run the Demo

From the repository root, run:

```bash
python demo.py
```

Then open:

- http://localhost:3000

This is a **local simulation**. It needs no Docker, no API server, and no `.env`,
and it never executes the commands it inspects.

The script does three things:

1. Replays a hardcoded four-step agent transcript
2. Scores each step's shell command with the real `RiskScorer` and `BlastRadiusEstimator`
3. Blocks the destructive step (`sudo rm -rf /var/lib/postgresql/data`) before it runs

### What You Should See

In the terminal:

- Three steps reported as allowed
- A `BLOCKED` line for the database wipe, with its risk score, blast radius, and the rule that triggered
- A summary panel explaining that the agent was stopped and nothing was deleted

### What the Demo Does Not Do

It does not contact the API, the database, or the dashboard — there is no
WebSocket traffic and no session is registered. It is a zero-config way to see
the safety engine make a decision.

To watch a live session reach the dashboard, start the stack with
`docker compose up -d` and connect an agent as described in the next section.

### Common Troubleshooting

- If the dashboard is blank, confirm `docker compose up -d` completed successfully and refresh http://localhost:3000.
- If port 3000, 8000, 5432, or 6379 is already in use, stop the conflicting service and restart the stack.

## 3. Connect Your First Agent

### Universal One-Liner (Any Framework)

If you don't want to use a specific adapter, or if you're using a custom agent, use the `watch()` wrapper. It auto-detects the framework and attaches the correct instrumentation:

```python
from panoptis import watch

# Wrap your existing agent object
agent = watch(your_agent)

# Use your agent as normal
result = agent.run("your task")
```

### Claude Code

The smallest working setup is to install AgentWatch and wrap your Claude Code command with the AgentWatch CLI:

```bash
panoptis session watch "Build me a REST API"
```

If you want to pin a model or change the safety policy, add options:

```bash
panoptis session watch "Build me a REST API" --model claude-opus-4-5 --policy default
```

What happens:

- AgentWatch captures the session
- Events appear in the live feed
- The session shows up in the sessions table
- Dangerous tool calls are blocked and logged

### LangChain

If you are using LangChain, install the LangChain extra first:

```bash
python -m pip install "panoptis[langchain]"
```

Then add the callback handler to your existing agent:

```python
from panoptis.adapters.langchain import AgentWatchCallbackHandler
from langchain.agents import AgentExecutor

handler = AgentWatchCallbackHandler(session_id="my-first-session")

agent = AgentExecutor(
    agent=...,  # your existing LangChain agent
    tools=...,  # your existing tools
    callbacks=[handler],
)
```

If you are calling a runnable or model directly, pass `callbacks=[handler]` to that call in the same way.

For the least setup possible, keep your current LangChain code and only add the callback handler.

## 4. Understanding the Dashboard

Open http://localhost:3000 after running the demo or your own agent. The main dashboard is split into five parts:

### Live Event Feed

This is the real-time stream of agent activity. It shows session starts, planner output, tool calls, tool results, and blocked actions as they happen.

### Sessions Table

This table lists recent sessions with:

- Session ID
- Framework
- Status
- Event count
- Token usage
- Estimated cost
- Start time

Click a session row to open the session detail page.

### Safety Blocks

This panel shows commands that were blocked by policy, along with the reason they were stopped. It is the fastest way to verify that AgentWatch is intercepting risky actions.

### Confidence Trends

This chart shows recent session confidence scores. Use it to spot sessions that are drifting, unstable, or likely to fail.

### Tool Traces

Tool traces live on the session detail page. Open a session from the table to see the execution trace, expand individual steps, review commands and outputs, and inspect rollback options.

### Screenshot

![AgentWatch dashboard](https://github.com/user-attachments/assets/51d4d77a-d377-4235-8f9c-193e717f9a64)

## 5. Configure Alerts

AgentWatch can send alerts to Slack and PagerDuty when risky activity is detected.

### Slack Webhook

Create a Slack incoming webhook and add it to your environment:

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
```

## 6. Security & Authentication

AgentWatch follows a **fail-closed** security model for production environments.

- **Development:** Unauthenticated access is allowed by default if `AGENTWATCH_API_KEY` is empty.
- **Production:** If `AGENTWATCH_ENV=production`, the API **requires** a valid `AGENTWATCH_API_KEY`. If the key is missing or unconfigured, all protected requests will be rejected with a `500 Server Misconfiguration` error to prevent unauthorized data exposure.

Always ensure `AGENTWATCH_API_KEY` is set in your production environment variables.

### Example `.env`

If you are running outside Docker Compose, copy the [.env.example](https://github.com/sreerevanth/AgentWatch/blob/main/.env.example) template file at the repository root to `.env` and fill in the values:

```bash
cp .env.example .env
```

The template includes standard defaults:

```bash
DATABASE_URL=postgresql+asyncpg://postgres:your_password_here@localhost:5432/panoptis
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
AGENTWATCH_ENV=development
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
PAGERDUTY_WEBHOOK_URL=https://events.pagerduty.com/v2/enqueue
```

### Enable Notifications

1. Add the webhook URL(s).
2. Restart the API so it picks up the new environment.
3. Trigger a blocked or high-risk action, such as the demo command that tries to delete `/var/log`.
4. Confirm the alert arrives in Slack, and PagerDuty if configured.

AgentWatch sends Slack alerts for any configured webhook and pages to PagerDuty only when the event risk meets the default high-risk threshold.

## Fast Check

When everything is working, you should be able to complete this flow in order:

1. Install `panoptis`
2. Start the stack with `docker compose up -d`
3. Run `python demo.py`
4. Open http://localhost:3000
5. See a live session, a safety block, and a confidence chart update

If you get stuck, start by checking whether the API is healthy at http://localhost:8000/health.
