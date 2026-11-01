# Claude Code Adapter

AgentWatch provides a first-class CLI wrapper for [Claude Code](https://github.com/anthropics/claude-code), allowing you to observe its reasoning steps, tool calls, and apply safety policies without modifying any code.

## Usage

Run your task through `panoptis session watch`:

```bash
panoptis session watch "Build me a React component for a weather dashboard"
```

## How It Works

1. **Subprocess Interception**: AgentWatch runs Claude Code as a child process.
2. **Stream Parsing**: It captures the terminal output and parses reasoning traces in real-time.
3. **Safety Enforcement**: If Claude Code attempts a dangerous command (e.g., `rm -rf /`), AgentWatch identifies the intent and can block the execution based on your policy.
4. **Dashboard Integration**: Every step is streamed over WebSockets to your local AgentWatch dashboard.

## Configuration Options

### Pinning a Model
`session watch` passes a model to Claude Code (default `claude-opus-4-5`). Choose another with `--model`:

```bash
panoptis session watch "..." --model claude-sonnet-5
```

### Applying a Safety Policy
Choose one of the built-in safety policies, `default`, `strict` or `permissive`:

```bash
panoptis session watch "..." --policy strict
```

## Benefits for Claude Code Users

- **Auditability**: See exactly what Claude did while you were away from the terminal.
- **Safety**: Prevent accidental file deletions or credential leaks.
- **Cost Tracking**: Monitor the token cost of complex coding tasks.
