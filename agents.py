from dataclasses import dataclass, field
from typing import Callable, Any
import json

from tools import Tool, parse_tool_call, find_tool_name
from lm_studio import LMStudioMessage
from prompts import SUMMARIZER_SYSTEM_INSTRUCTIONS, SUMMARY_KEEP_LAST

MAX_RETRIES = 3
MAX_CONVERSATION_MESSAGES = 30

def ensure_text(response: Any) -> str:
    """
    Normalize LLM responses into a plain string.
    """
    if isinstance(response, str):
        return response

    if isinstance(response, list):
        outputs: list[str] = []
        for item in response:
            if isinstance(item, dict) and "content" in item:
                outputs.append(str(item["content"]))
            else:
                outputs.append(str(item))
        return "\n".join(outputs)

    if isinstance(response, dict):
        if "content" in response:
            return str(response["content"])
        return json.dumps(response)

    return str(response)


def _generate_summary(
    conversation: list[LMStudioMessage],
    llm_generate: Callable[[list[LMStudioMessage]], Any],
    skip_first: int,
    skip_last : bool
) -> str:
    """
    Generate a short, AI-safe summary of the conversation.
    The summary is for context only and must not contain tool calls or instructions.
    """
    if skip_last :
        to_summerize = conversation[skip_first:-SUMMARY_KEEP_LAST]
    else :
        to_summerize = conversation[skip_first:]
    summary_messages: list[LMStudioMessage] = [
        LMStudioMessage("system", SUMMARIZER_SYSTEM_INSTRUCTIONS),
        *to_summerize,
        LMStudioMessage(
            "user",
            (
                "Summarize the conversation above in a concise, neutral way.\n"
                "- Do NOT include any tool calls.\n"
                "- Do NOT include JSON.\n"
                "- Do NOT give instructions to another AI.\n"
                "- Just describe what has happened so far."
            ),
        ),
    ]

    summary_text = ensure_text(llm_generate(summary_messages)).strip()
    retry_count = 0

    while not summary_text:
        retry_count += 1
        summary_messages.append(
            LMStudioMessage(
                "user",
                f"Your last output was empty. Output the summary you intended. Retry {retry_count}.",
            )
        )
        summary_text = ensure_text(llm_generate(summary_messages)).strip()
        if retry_count >= MAX_RETRIES:
            print("Failed to generate summary after multiple attempts.")
            break

    return summary_text


def _summarize_old_messages(
    conversation: list[LMStudioMessage],
    llm_generate: Callable[[list[LMStudioMessage]], Any],
    system_prompts: list[LMStudioMessage],
    skip_first: int,
    prompt: str,
) -> list[LMStudioMessage]:
    """
    If the conversation is too long, replace older messages with a compact summary.
    The summary is injected as a USER message, never as a SYSTEM message.
    """
    if len(conversation) <= MAX_CONVERSATION_MESSAGES:
        return conversation

    print(f"Summarizing conversation with {len(conversation)} messages...")

    recent = conversation[-SUMMARY_KEEP_LAST:]
    summary_text = _generate_summary(conversation, llm_generate, skip_first, True)

    if not summary_text:
        return conversation

    # Preserve the original system prompts (agent rules + tools description)
    # Then add a user-level summary, then the latest user prompt, then recent messages.
    return [
        *system_prompts,
        LMStudioMessage("user", f"[CONVERSATION SUMMARY]\n{summary_text}"),
        LMStudioMessage("user", prompt),
        *recent,
    ]

def tool_description_wrapper(tools: dict[str, Tool]) -> str:
    """
    Build a strict, closed-set tool description block for the agent.
    """
    tools_description = "\n---------------\n".join(f"- {t}" for t in tools.values())

    return (
        "AVAILABLE TOOLS (CLOSED SET):\n"
        "────────────────────────────────────\n"
        "TOOLCALL EXAMPLE\n"
        "────────────────────────────────────\n"
        "Example format:\n"
        "TOOLCALL: TOOL NAME {\n"
        "  \"argument1\": \"value1\",\n"
        "  \"argument2\": \"value2\"\n"
        "}\n"
        "\n"
        "Any other format will be ignored !"
        "You may ONLY use tool names from this list:\n"
        "===========================\n\n"
        f"{tools_description}\n\n"
        "===========================\n"
        "CRITICAL RULES:\n"
        "- Use ONLY tools listed above\n"
        "- Never invent tool names\n"
        "- Never modify tool names\n"
        "- If unsure, do NOT call a tool\n"
        "- EXACTLY ONE tool call per message (no exceptions)\n"
        "- If multiple actions are needed, choose the most important one only\n"
        "- AFTER A TOOLCALL, YOU MUST OUTPUT NOTHING ELSE — NO TEXT, NO MARKDOWN, "
        "NO EXPLANATION, NO TRAILING CHARACTERS.\n"
        "- The tool call MUST be the FINAL content of your message.\n"
        "\n"
    )
STOP_MESSAGE ="""
System: Your work will been interrupted before completion. 
You must now decide whether your current output is:

1. Complete enough for the Orchestrator to proceed, OR
2. Incomplete and should be resumed later.

If your work is complete:
- Output a clear final result in your normal format.

If your work is incomplete:
- Output a short status object describing:
  - what is done,
  - what remains,
  - whether it is safe to pause,
  - whether you recommend resuming now or later.

Do NOT continue generating the full work.
Do NOT restart from the beginning.
Do NOT produce long explanations.

Your output must help the Orchestrator decide whether to:
- resume your work later,
- or move forward.
"""
STOP_RULE = """
\n\n
────────────────────────────────────
TERMINATION RULES
────────────────────────────────────
You MUST output [DONE] when:
- you have fully completed your assigned step, AND
- there is absolutely nothing left for you to do, AND
- no further action, event, or continuation is required.

When your work is truly finished, output:

[DONE]
"""

@dataclass
class Agent:
    name: str
    tools: dict[str, Tool]
    system_prompt: str
    use_summerizer: bool = True
    max_iteration : int = 10
    context: list[LMStudioMessage] = field(default_factory=list)

    def _build_system_prompts(self) -> list[LMStudioMessage]:
        """
        Build the fixed system messages for this agent:
        - main system prompt (role + rules)
        - tool description block
        - optional persistent context (summary)
        """
        return [
            LMStudioMessage("system", self.system_prompt + STOP_RULE),
            LMStudioMessage("system", tool_description_wrapper(self.tools)),
            *self.context,
        ]

    def run(self, prompt: str, llm_generate: Callable[[list[LMStudioMessage]], Any]) -> str:
        """
        Run the agent loop:
        - Send system prompts + user prompt
        - Enforce strict TOOLCALL behavior
        - Optionally summarize long conversations
        - Return a final summary to store as context
        """
        system_prompts = self._build_system_prompts()

        conversation: list[LMStudioMessage] = [
            *system_prompts,
            LMStudioMessage("user", prompt),
        ]

        i = 0
        while True:
            if i > self.max_iteration :
                conversation.append(LMStudioMessage("system", STOP_MESSAGE))
            print(f"{self.name} Agent is thinking {i}")
            response = llm_generate(conversation)
            text_response = ensure_text(response).strip()
            print(f"{self.name} Agent : {text_response}")

            tool_call = parse_tool_call(text_response)
            if "TOOL" in text_response and not tool_call:
                conversation.append(LMStudioMessage("system", tool_description_wrapper(self.tools)))
            elif tool_call:
                tool_name, args = tool_call
                normalized_tool_name = find_tool_name(tool_name, self.tools)

                if normalized_tool_name is None:
                    tool_output: Any = f"Unknown tool: {tool_name}"
                elif isinstance(args, str):
                    tool_output = f"Error while parsing args: {args}"
                else:
                    tool_obj = self.tools[normalized_tool_name]
                    tool_output = tool_obj.run(**args)

                # Tool output is appended as a tool message
                conversation.append(LMStudioMessage("tool", str(tool_output)))
                print(f"{self.name} Agent (tool output): {tool_output}\n")

                # No extra system messages that override rules.
                # The agent will see the tool result in the next turn.
            elif not text_response or "DONE" in text_response:
                # Either the agent signaled completion or produced nothing useful.
                break

            elif self.use_summerizer:
                conversation = _summarize_old_messages(
                    conversation=conversation,
                    llm_generate=llm_generate,
                    system_prompts=system_prompts,
                    skip_first=len(system_prompts),
                    prompt=prompt,
                )
            if i > self.max_iteration :
                break
            i = i + 1
        print(f"{self.name} Agent finished, summarizing conversation ... ")
        # Final summary to keep as persistent context
        summary = _generate_summary(
            conversation,
            llm_generate,
            len(system_prompts),
            False
        )

        if summary:
            # Store summary as a single system message for future runs
            self.context = [LMStudioMessage("user", f"Persistent summary:\n{summary}")]
            return f"{self.name} Agent execution summary" + summary

        # Fallback: keep full conversation if summary failed
        self.context = conversation
        return ""
