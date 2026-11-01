# Extended Getting Started Guide

## Setup Configuration
Declare parameters in a local `.env` file:
```env
AGENTWATCH_ENV=development
DATABASE_URL=sqlite:///agentwatch.db
REDIS_URL=redis://localhost:6379/0
```

## First Instrumented Execution
Verify setup by wrapping a dummy model agent:
```python
from panoptis import watch

class TestAgent:
    def run(self, query: str) -> str:
        return f"Response to: {query}"

agent = watch(TestAgent())
agent.run("Verify connection parameters.")
```
Confirm execution logs indicate `SESSION_START` hooks.
