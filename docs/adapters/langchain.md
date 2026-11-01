# LangChain Adapter

The AgentWatch LangChain adapter provides seamless instrumentation for LangChain agents and chains by hooking into the standard Callback system.

## Installation

```bash
pip install "panoptis[langchain]"
```

## Basic Usage

The most common way to use the adapter is to pass it to the `callbacks` list of your `AgentExecutor` or LLM.

```python
from panoptis.adapters.langchain import AgentWatchCallbackHandler
from langchain.agents import AgentExecutor

# Initialize the handler
handler = AgentWatchCallbackHandler(
    session_id="my-session-123", # Optional: defaults to a new UUID
    agent_id="my-agent-456"      # Optional: helpful for multi-agent setups
)

# Use with AgentExecutor
agent = AgentExecutor(
    agent=..., 
    tools=..., 
    callbacks=[handler]
)

# Use with an LLM directly
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4", callbacks=[handler])
```

## Key Features

- **Prompt Tracing**: Captures the exact prompts sent to the LLM.
- **Tool Execution Tracking**: Logs every tool call, including arguments and results.
- **Token Usage**: Automatically tracks input and output tokens for cost calculation.
- **Safety Integration**: If the safety engine is active, dangerous tool calls can be blocked pre-execution.

## Advanced Configuration

You can customize the event bus if you need to route events to a custom backend:

```python
from panoptis.core.event_bus import EventBus
from panoptis.adapters.langchain import AgentWatchCallbackHandler

custom_bus = EventBus()
handler = AgentWatchCallbackHandler(event_bus=custom_bus)
```
