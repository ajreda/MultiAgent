from typing import Any, Dict
from dataclasses import dataclass


@dataclass
class Middleware:
    """Base middleware class for agent/tool event hooks."""
    
    def on_agent_run(self, agent: "Agent", message: str) -> None:
        pass

    def on_tool_run(self, tool: "Tool", kwargs: Dict[str, Any]) -> None:
        pass

    def on_message_routed(self, src: str, dst: str, message: str) -> None:
        pass


class LoggingMiddleware(Middleware):
    """Middleware that logs all agent and tool interactions."""
    
    def __init__(self):
        self.logs: list[str] = []

    def on_agent_run(self, agent: "Agent", message: str) -> None:
        self.logs.append(f"Agent {agent.name} run with: {message}")

    def on_tool_run(self, tool: "Tool", kwargs: Dict[str, Any]) -> None:
        self.logs.append(f"Tool {tool.name} run with args: {kwargs}")

    def on_message_routed(self, src: str, dst: str, message: str) -> None:
        self.logs.append(f"Routed from {src} to {dst}: {message}")

    def get_logs(self) -> str:
        return "\n".join(self.logs)
