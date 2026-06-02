
import json
from tools import TOOL_REGISTRY, Tool, find_tool_name, EVENT_QUEUE, EVENT_ROUTING
from agents import Agent
from lm_studio import LMStudioClient
from middlewares import LoggingMiddleware
from prompts import (
    DEVELOPER_AGENT_PROMPT,
    PLANNER_AGENT_PROMPT,
    PRODUCT_OWNER_PROMPT,
    TECH_LEAD_PROMPT,
)
from prompts_old import ORCHESTRATOR_PROMPT

MAX_TOKENS = None
BACKEND = "qwen/qwen3.5-9b"

def _select_tools(*tool_names: str) -> dict[str, Tool]:
    selected = {}
    for name in tool_names:
        canonical = find_tool_name(name, TOOL_REGISTRY)
        if canonical is None:
            raise ValueError(f"Tool not found in registry: {name}")
        selected[canonical] = TOOL_REGISTRY[canonical]
    return selected


PLANNER_TOOLS = _select_tools(
    "Read Spec",
    "Create Plan",
    "Add Task",
    "Update Task Field",
    "Delete Task",
    "Read Plan",
    "Overwrite Plan"
)

PRODUCT_OWNER_TOOLS = _select_tools(
    "Create Product Spec",
    "List Specs",
    "Read Spec",
    "Delete Spec",
    "Add User Story",
    "Add Requirement",
    "Add Acceptance Criteria",
    "Add UX Flow",
    "Add API Contract",
    "List Specifications",
    "Ask Single Clarifying Question",
)

DEVELOPER_TOOLS = _select_tools(
    "Exist File",
    "Read File",
    "Read Task",
    "Get Created Files Paths",
    "Create File Skeleton",
    "Append Small Patch",
    "Insert Line In File",
    "Replace In File",
    "Ask Single Clarifying Question"
)

ORCHESTRATOR_TOOLS = _select_tools(
    "Emit Event",
    "List Specs",
    "Read Plan",
    "Read Spec",
)

TECH_LEAD_TOOLS = _select_tools(
    "Read File",
    "Read Plan",
    "Get Created Files Paths",
    "Update Task State",
    "Ask Single Clarifying Question",
)

planner_agent = Agent("Planner", PLANNER_TOOLS, PLANNER_AGENT_PROMPT, max_iteration=15)
product_owner_agent = Agent("Product Owner", PRODUCT_OWNER_TOOLS, PRODUCT_OWNER_PROMPT, max_iteration=15)
tech_lead_agent = Agent("Tech Lead", TECH_LEAD_TOOLS, TECH_LEAD_PROMPT, max_iteration=5)
developer_agent = Agent("Developer", DEVELOPER_TOOLS, DEVELOPER_AGENT_PROMPT, max_iteration=10)
orchestrator_agent = Agent("Orchestrator", ORCHESTRATOR_TOOLS, ORCHESTRATOR_PROMPT, max_iteration=5)

client = LMStudioClient(BACKEND, default_max_tokens=MAX_TOKENS)
logger = LoggingMiddleware()

class AgentSystem:
    def __init__(self, 
                 client: LMStudioClient,
                 logger: LoggingMiddleware | None = None):
        self.client = client
        self.logger = logger or LoggingMiddleware()
        self.agents = {
            "planner": planner_agent,
            "product_owner": product_owner_agent,
            "developer": developer_agent,
            "tech_lead": tech_lead_agent,
            "orchestrator": orchestrator_agent,
        }

    def _resolve_agent(self, recipient: str | None):
        if not recipient:
            return self.agents["orchestrator"]

        normalized = recipient.strip().lower()
        if normalized in self.agents:
            return self.agents[normalized]

        if normalized == "chatagent":
            return self.agents["developer"]

        return self.agents["orchestrator"]

    def _agent_for_event(self, event_type: str):
        normalized = event_type.strip().lower()
        agent_name = EVENT_ROUTING.get(normalized)
        if agent_name :
            return self.agents.get(agent_name)
        return None

    def _format_event_prompt(self, event: dict[str, object]) -> str:
        payload = event.get("payload") or {}
        payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
        return (
            f"Event received: {event['type']}\n"
            f"Payload:\n{payload_json}\n"
        )

    def _dispatch_events(self):
        trace = []

        while EVENT_QUEUE:
            event = EVENT_QUEUE.pop(0)

            agent = self._agent_for_event(event["type"])
            if not agent:
                trace.append(f"Unhandled event: {event['type']}")
                continue

            prompt = self._format_event_prompt(event)

            result = agent.run(prompt, self.client.generate)

            trace.append({
                "event": event,
                "agent": agent.name,
                "result": result
            })

        return trace
    
    def _build_orchestrator_summary(self, trace):
        return (
            "You are the Orchestrator reviewing a completed execution batch.\n\n"
            "Here is what happened:\n\n"
            f"{json.dumps(trace, indent=2)}\n\n"
            "Decide the next action:\n"
            "- continue tasks\n"
            "- re-plan\n"
            "- unblock issues\n"
            "- assign new work\n"
        )
    
    def send(self, sender: str, message: str) -> str:
        orchestrator = self._resolve_agent("orchestrator")

        # 1) User → Orchestrator
        self.logger.on_message_routed(sender, orchestrator.name, message)
        response = orchestrator.run(message, self.client.generate)

        # 2) Process all events generated by orchestrator
        while EVENT_QUEUE:
            trace = self._dispatch_events()

            if trace:
                summary_prompt = self._build_orchestrator_summary(trace)

                self.logger.on_message_routed(
                    "EventBus",
                    "Orchestrator",
                    summary_prompt
                )

                orchestrator = self.agents["orchestrator"]
                response = orchestrator.run(summary_prompt, self.client.generate)
        return response


def main():
    system = AgentSystem(client, logger)
    prompt = (
        "You were working on the following project:\n"
        "Create a webpage to allow me to show and sell my artwork to my friends \n"
        "But you decided for no reason to stop after the plan creation step ! \n"
        "Please continue the work and proceed to the next steps !"
    )
    print(system.send("User", prompt))


if __name__ == "__main__":
    main()
