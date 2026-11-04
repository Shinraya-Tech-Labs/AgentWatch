"""
Demo agent for AgentWatch — exercises the full watch() → dashboard pipeline.

Usage:
    # Terminal 1: start the API server
    panoptis server start

    # Terminal 2: run this script
    python real_agent.py

Then open http://localhost:3000 (or http://localhost:8000/api/v1/sessions)
and watch the session appear in real time.
"""

import asyncio

from panoptis import watch


class DemoAgent:
    """Simulates an AI agent that performs safe and one dangerous action."""

    async def run(self, goal: str = "Analyze project and generate report") -> None:
        print(f"\n[Agent] Goal: {goal}")

        # These execute() calls are intercepted by watch().
        # Each is checked by the safety engine before running.
        await self.execute("cat config.yaml")
        await self.execute("SELECT * FROM users LIMIT 10")
        await self.execute("echo '{}' > report.json")

        print("\n[Agent] Attempting dangerous command...")
        try:
            # rm -rf on a root path matches the CRITICAL pattern → blocked
            await self.execute("rm -rf /important/data")
            print("[Agent] (should not reach here — command should be blocked)")
        except Exception as exc:
            print(f"[Agent] Blocked by AgentWatch: {exc}")

        await self.execute("curl https://api.example.com/report")
        print("\n[Agent] Session complete.")

    async def execute(self, command: str) -> str:
        """Generic execution entry point — intercepted by watch() as a tool call."""
        print(f"[Agent] execute: {command}")
        return f"ok: {command}"


if __name__ == "__main__":
    agent = DemoAgent()
    watched = watch(agent)  # registers HTTP forwarder → localhost:8000
    asyncio.run(watched.run())
