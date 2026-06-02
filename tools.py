import ast
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional
from pathlib import Path
WORKSPACE_ROOT = Path("./workspace").resolve()

FORBIDDEN_EXTENSIONS = {
    ".exe", ".dll", ".so", ".bin", ".env"
}
ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".html",
    ".css", ".json", ".md"
}
PLAN_PATH = WORKSPACE_ROOT / "plan" / "plan.md"
SRC_ROOT = WORKSPACE_ROOT / "src"


def get_plan_path() -> Path:
    return PLAN_PATH

def get_src_root() -> Path:
    return SRC_ROOT

def resolve_src_path(filepath: str) -> Path:
    if not filepath:
        raise ValueError("Error: No filepath provided.")
    if os.path.isabs(filepath):
        raise ValueError("Error: Absolute paths are not allowed.")
    normalized = filepath.replace("\\", "/")
    if ".." in normalized:
        raise ValueError("Error: Path traversal is not allowed.")
    target = (SRC_ROOT / normalized).resolve()
    if not str(target).startswith(str(SRC_ROOT)):
        raise ValueError("Error: filepath escapes workspace src root.")
    return target

def load_plan() -> str:
    path = get_plan_path()
    if not path.exists():
        return "No plan available"
    with open(path, "r", encoding="UTF8") as f :
        return f.read()

def save_plan(content: str) -> str:
    path = get_plan_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="UTF8") as f :
        f.write(content)
    return str(path)
def load_json_plan() -> dict[str, dict]:
    if not PLAN_PATH.exists():
        return {"tasks": {}}
    return json.loads(PLAN_PATH.read_text())

def save_json_plan(plan: dict):
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(json.dumps(plan, indent=2))
    
@dataclass
class Tool:
    name: str
    description: str
    function: Callable[..., Any]
    user_input : bool
    def __str__(self) -> str:
        return f"{self.name}: {self.description}"
    __repr__ = __str__
    def run(self, **kwargs) -> Any:
        try:
            result = self.function(**kwargs)
            tool_output = {
                "success": True,
                "tool": self.name,
                "arguments": kwargs,
                "result": result,
            }
        except Exception as e:
            tool_output = {
                "success": False,
                "tool": self.name,
                "arguments": kwargs,
                "error": str(e),
                "error_type": type(e).__name__,
            }
        return tool_output

TOOL_REGISTRY: dict[str, Tool] = {}
TOOL_CALL_TOKEN = "TOOLCALL:"
MEMORY : dict[str, Any] = {}


def tool(name: str | None = None, description: str = "",
         aliases: list[str] | None = None,
         registry : dict[str, Tool] | None = None,
         user_input: bool = False):

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        tool_name = name or fn.__name__.replace("_", " ").title()
        tool_desc = description or (fn.__doc__ or "").strip()
        tool_obj = Tool(name=tool_name, description=tool_desc, function=fn, user_input=user_input)
        target_registry = TOOL_REGISTRY if registry is None else registry
        target_registry[tool_name] = tool_obj
        for a in aliases or []:
            target_registry[a] = tool_obj
        return fn

    return decorator


def normalize_tool_name(name: str) -> str:
    return name.strip().lower().replace("_", " ")


def parse_tool_call(text: Any) -> tuple[str, dict | str] | None:
    if text is None:
        return None

    if isinstance(text, list):
        items = []
        for item in text:
            if isinstance(item, dict) and "content" in item:
                items.append(str(item["content"]))
            else:
                items.append(str(item))
        text = "\n".join(items)

    if isinstance(text, dict):
        if "content" in text:
            text = str(text["content"])
        else:
            text = json.dumps(text)

    text = str(text).strip()
    if not text:
        return None

    # Support JSON tool call body
    if text.startswith("{"):
        try:
            payload = json.loads(text)
            tool_name = payload.get("tool")
            args = payload.get("args", {})
            if tool_name and isinstance(args, dict):
                return tool_name, args
        except json.JSONDecodeError:
            pass

    # Support simple TOOLCALL syntax across one or more lines
    lower_text = text.lower()
    token_index = lower_text.find(TOOL_CALL_TOKEN.lower())
    if token_index >= 0:
        rest = text[token_index + len(TOOL_CALL_TOKEN):].strip()
        if not rest:
            return None
        tool_name, args_str = _extract_tool_name_and_args(rest)
        if not tool_name:
            return None
        args = _parse_tool_args(args_str)
        return tool_name, args
    return None


def _extract_tool_name_and_args(payload: str) -> tuple[str, str]:
    """
    Find the longest matching registered tool name in the payload.
    Supports multi-word tool names such as "Run Code" or "Create New File".
    Also strips code fences and extracts JSON args starting at the first '{'.
    """

    # 1. Normalize input
    normalized_payload = payload.strip()

    # 2. Strip code fences if present
    if normalized_payload.startswith("```"):
        normalized_payload = normalized_payload.lstrip("`").strip()
    if normalized_payload.endswith("```"):
        normalized_payload = normalized_payload.rstrip("`").strip()

    # 3. Try matching tool names (longest first)
    candidates = sorted(TOOL_REGISTRY.keys(), key=lambda n: len(n), reverse=True)

    for tool_name in candidates:
        pattern = r"^" + re.escape(tool_name) + r"(\s+|:|$)"
        match = re.match(pattern, normalized_payload, flags=re.IGNORECASE)

        if match:
            # Extract everything after the tool name
            raw_args = normalized_payload[match.end():].strip()
            # If JSON args exist, extract from the first '{'
            brace_index = raw_args.find("{")
            if brace_index != -1:
                return tool_name, raw_args[brace_index:].strip()

            # Otherwise return raw args as-is
            return tool_name, raw_args

    # No tool name matched
    return "", payload


def _parse_tool_args(args_str: str) -> dict | str:
    if not args_str.strip():
        return {}

    try:
        parsed = json.loads(args_str)

        if isinstance(parsed, dict):
            return parsed

        return f"{args_str} : Not a valid JSON object"

    except json.JSONDecodeError as e:
        try:
            parsed = ast.literal_eval(args_str)
            if isinstance(parsed, dict): 
                return parsed
        except: 
            pass        
        return (
            "ERROR: Failed to parse tool arguments.\n"
            "The arguments MUST be valid JSON.\n\n"
            f"Received:\n{args_str}\n\n"
            f"Parser error:\n{e}"
        )

def find_tool_name(tool_name: str, toolset : dict[str, Tool]) -> str | None:
    normalized = normalize_tool_name(tool_name)
    for key in toolset:
        if normalize_tool_name(key) == normalized:
            return key
    return None
@tool(name="Exist File")
def exist_file(filepath: str, **args) -> bool:
    """
        Check if a file exists at the given path inside src root.
        Arguments:
        - filepath: string
    """
    try:
        target_path = resolve_src_path(filepath)
    except ValueError:
        return False
    return target_path.exists()
# --- deafult Tool Definitions ---
@tool(name="Read File")
def read_file_tool(filepath: str, start_line: int | None = None, end_line: int | None = None, **args) -> str:
    """
    Read an existing file, optionally returning only a slice of lines.

    Arguments:
    - filepath: string (required)
    - start_line: 1-based index of the first line to read (optional)
    - end_line: 1-based index of the last line to read (optional)

    Behavior:
    - If no start_line/end_line are provided, the entire file is returned.
    - If only start_line is provided, returns from stwart_line to the end.
    - If both are provided, returns the inclusive slice [start_line, end_line].
    - Out-of-range indices are handled gracefully.
    """

    if not filepath:
        return "No filepath provided"

    try:
        target_path = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target_path.exists():
        return f"File not found: {filepath}"

    with open(target_path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    # No slicing → return full file
    if start_line is None and end_line is None:
        return "\n".join(lines)

    # Normalize indices
    start = max(1, start_line or 1)
    end = end_line if end_line is not None else len(lines)

    # Convert to 0-based slice
    start_idx = start - 1
    end_idx = end

    sliced = lines[start_idx:end_idx]
    return "\n".join(sliced)

@tool(name="Append Small Patch")
def append_small_patch(filepath: str, patch: str, **args) -> str:
    """
    Append a very small localized patch to a file.

    This tool is ONLY for tiny incremental edits.
    Large content blocks are forbidden.

    Constraints:
    - maximum 1-3 lines
    - maximum 500 characters
    - workspace-only writes

    Arguments:
    - filepath: relative path inside the workspace
    - patch: tiny text patch to append

    Returns:
    - confirmation message
    """

    try:
        target_path = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if target_path.suffix.lower() in FORBIDDEN_EXTENSIONS:
        return "Error: forbidden file type."

    target_path.parent.mkdir(parents=True, exist_ok=True)

    with open(target_path, "a", encoding="utf-8") as f:
        if target_path.stat().st_size > 0:
            f.write("\n")

        f.write(patch)

    return f"Small patch appended to {filepath}"

@tool(name="Get Created Files Paths")
def get_created_files_paths(ext: str | list[str] | None = None, **args) -> str:
    """
    Retrieve the list of files available, optionally filtered by extension.

    ARGUMENTS:
    - ext: a single extension (".html") or a list of extensions ([".html", ".css"])
           If None, all files are returned.

    Behavior:
    - Scans the src root recursively.
    - Ignores ALL folders whose names start with "." (e.g. .git, .vscode, .config).
    - Extensions are matched case-insensitively.
    - Extensions may be provided with or without the leading dot.

    Returns:
    - A comma-separated string of file paths.
    """

    folder = get_src_root()
    if not folder.exists():
        return "Workspace folder is empty. Use Create File Skeleton to create your first file."

    # Normalize extension(s)
    if ext is None:
        exts = None
    else:
        if isinstance(ext, str):
            exts = [ext]
        else:
            exts = ext

        exts = [
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in exts
        ]

    files = []

    for root, dirs, filenames in os.walk(folder):
        # 🔥 Ignore hidden folders (starting with ".")
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for name in filenames:
            # Skip hidden files too
            if name.startswith("."):
                continue

            rel = os.path.relpath(os.path.join(root, name), folder)

            if exts is not None:
                _, extension = os.path.splitext(name)
                if extension.lower() not in exts:
                    continue

            files.append(rel)

    if not files and ext:
        return f"The workspace doesn't contain any file with the following extension(s) {ext}"

    if not files:
        return "The workspace is Empty."

    return ", ".join(files)


@tool(name="Replace In File")
def replace_in_file(
    filepath: str,
    old_text: str,
    new_text: str,
    **args
) -> str:
    """
    Replace a piece of text in a file.

    Purpose:
    - Update TODOs or placeholders
    - Make small, localized edits
    - Replace simple markers or short snippets

    Rules:
    - Only the first occurrence of old_text is replaced.
    - new_text should be short (a few lines).
    - Not intended for large rewrites or full implementations.

    Arguments:
    - filepath: path to the file
    - old_text: exact text to replace
    - new_text: replacement content

    Returns:
    - A confirmation message or an error message.
    """
    try:
        target_path = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target_path.exists():
        return f"File not found: {filepath}"


    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    anchor = find_anchor(content, old_text)

    if not anchor:
        return "Anchor not found."
    updated = content.replace(anchor, new_text, 1)

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(updated)

    return (
        f"Text replaced successfully "
        f"in {filepath}"
    )
@tool(name="Replace Line In File")
def replace_line_in_file(filepath: str, line_number: int, new_text: str, **args) -> str:
    """
    Replace the content of the N-th line inside a file with a tiny localized patch.

    This tool is ONLY for:
    - TODO refinement
    - placeholder finalization
    - tiny localized edits
    - surgical line-level corrections

    It MUST NOT be used for:
    - large rewrites
    - full implementations
    - replacing large sections

    Constraints:
    - new_text should normally be 1–3 lines
    - new_text must stay under 500 characters
    - line_number is 1-based
    - if the line does not exist, empty lines will be created until it does

    Arguments:
    - filepath: relative workspace path
    - line_number: the 1-based index of the line to replace
    - new_text: the new content for that line

    Returns:
    - confirmation message
    """

    try:
        target_path = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target_path.exists():
        return f"File not found: {filepath}"

    with open(target_path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    # Ensure file has enough lines
    while len(lines) < line_number:
        lines.append("")

    # Replace the target line
    lines[line_number - 1] = new_text

    updated = "\n".join(lines)

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(updated)

    return (
        f"Line {line_number} replaced successfully "
        f"in {filepath}"
    )

@tool(name="Insert Line In File")
def insert_line_in_file(filepath: str, line_number: int, new_text: str, position: str = "before", **args) -> str:
    """
    Insert a small piece of text into a file at a specific line.

    Purpose:
    - Insert text BEFORE or AFTER a given line number.
    - Useful for incremental edits and building files step by step.

    Rules:
    - new_text should be short (a few lines).
    - line_number is 1-based.
    - If the line does not exist, blank lines are added until it does.
    - position must be "before" or "after".

    Arguments:
    - filepath: path to the file
    - line_number: target line (1-based)
    - new_text: text to insert
    - position: "before" (default) or "after"

    Returns:
    - A confirmation message or an error message.
    """

    try:
        target_path = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target_path.exists():
        return f"File not found: {filepath}"

    with open(target_path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    # Ensure enough lines exist
    while len(lines) < line_number:
        lines.append("")

    insert_index = line_number - 1
    if position == "after":
        insert_index += 1

    # Insert each line of new_text (in reverse to preserve order)
    for line in reversed(new_text.split("\n")):
        lines.insert(insert_index, line)

    updated = "\n".join(lines)

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(updated)

    return f"Inserted line(s) {position} line {line_number} in {filepath}."

@tool(name="Run Python Code From File")
def run_file(filepath: str, **args) -> str:
    """
    Execute a Python code located in the specified file and return its output.
    Arguments:
    - filepath: full path to the Python file.
    Returns:
    - The execution output or an error message.
    """
    try:
        target = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target.exists():
        return f"File not found: {filepath}"

    try:
        with open(target, "r", encoding="utf-8") as f:
            code = f.read()
        return run_code_tool(code)
    except Exception as e:
        return f"Execution error: {e}"
@tool(name="Insert Line In Code")
def insert_line_in_code(line_number: int, new_text: str, position: str = "before", **args) -> str:
    """
    Insert a tiny patch into the in-memory code buffer at a specific line.

    PURPOSE:
    - Insert a new line either BEFORE or AFTER the given line number.
    - Designed for tiny, surgical edits (1–3 lines max).
    - Perfect for TODO expansion, adding imports, adding a function signature, etc.

    RULES:
    - new_text MUST be tiny (1–3 lines, <500 chars).
    - line_number is 1-based.
    - If the line does not exist, empty lines are created until it does.
    - position MUST be "before" or "after".

    Arguments:
    - line_number: target line index (1-based)
    - new_text: the line(s) to insert
    - position: "before" (default) or "after"

    Returns:
    - confirmation message
    """

    existing = MEMORY.get("code", "")
    lines = existing.split("\n")

    # Ensure enough lines exist
    while len(lines) < line_number:
        lines.append("")

    insert_index = line_number - 1
    if position == "after":
        insert_index += 1

    for line in reversed(new_text.split("\n")):
        lines.insert(insert_index, line)

    updated = "\n".join(lines)
    MEMORY["code"] = updated

    return f"Inserted line(s) {position} line {line_number} in code buffer."

@tool(name="Create File Skeleton")
def create_file_skeleton_tool(filepath: str, skeleton: str = "") -> str:
    """
    Create a new file with a lightweight structural scaffold.

    This tool is for:
    - creating empty files
    - adding basic structure (imports, placeholders, TODOs)
    - setting up minimal boilerplate

    This tool is NOT for:
    - writing full implementations
    - generating large components or long code blocks

    Arguments:
    - filepath: path of the file to create
    - skeleton: optional short scaffold or placeholder content

    Returns:
    - A confirmation message or an error message.
    """

    try:
        target = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not filepath:
        return "Error: No filepath provided."

    target.parent.mkdir(parents=True, exist_ok=True)

    # Write file
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(skeleton)
    except Exception as e:
        return f"Error writing file '{target}': {e}"

    return f"File created: {filepath} ({len(skeleton)} bytes written)"

@tool(name="Run Code")
def run_code_tool(code: str, timeout: int = 10, **args) -> str:
    """
    Execute Python code and return stdout/stderr.
    Arguments :
    - code: string of Python code to execute
    - timeout: maximum seconds to allow for code execution, default is 10 seconds
    """
    import subprocess
    import sys

    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False
        )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 0:
            return (
                f"Return code: {completed.returncode}\n"
                f"STDOUT:\n{stdout}\n"
                f"STDERR:\n{stderr}"
            )
        if stderr:
            return f"STDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        return f"STDOUT:\n{stdout}"
    except subprocess.TimeoutExpired:
        return f"Execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Error running code: {e}"


@tool(name="Ask Single Clarifying Question", user_input=True)
def ask_single_question(question: str, **args) -> str:
    """
    Ask exactly ONE short, focused question to the client.

    STRICT RULES:
    - The question MUST be a single sentence
    - It MUST ask only one thing (no AND / OR chaining)
    - It MUST be short and direct (max ~15–20 words)
    - It MUST NOT include explanations or context
    - It MUST NOT contain multiple sub-questions
    Arguments:
    - question : A short sentence explaining what the question.
    This tool is ONLY for minimal clarification needed to proceed.
    """
    response = input(question.strip() + "\nYour reply: ")
    return response
EVENT_QUEUE: list[dict] = []
EVENT_ROUTING = {
    "create_spec": "product_owner",
    "write_user_story": "product_owner",
    "plan_feature": "planner",
    "plan_story": "planner",
    "development_blocked": "planner",
    "implement_task": "developer",
    "revision_requested": "developer",
    "review_implementation": "tech_lead",
    "review": "tech_lead"
}

EMIT_EVENT_DESCRIPTION = f"""
    Emit a structured workflow event into the orchestrator event bus.

    Arguments:
    - event_type: short event name used by the orchestrator to route work. 
            Allowed event_type are {', '.join(EVENT_ROUTING.keys())}
    - payload: structured JSON-like data for the event.

    Returns:
    - str: confirmation message.
    """

@tool(name="Emit Event", description=EMIT_EVENT_DESCRIPTION, aliases=["emit event", "emit_event"])
def emit_event(event_type: str, payload: dict, **args) -> str:

    # Store event in a reserved location for the orchestrator
    # The orchestrator must make sure it reads this from the shared runtime.
    if not isinstance(payload, dict):
        return "Error: payload must be a dictionary."
    event_types = EVENT_ROUTING.keys()
    if not event_type in event_types:
        raise ValueError(
            f"'{event_type}' is not a valid event_type. "
            f"Valid event types are: {', '.join(EVENT_ROUTING.keys())}"
        )
    new_event = {
        "type": event_type,
        "payload": payload,
    }
    if new_event not in EVENT_QUEUE:
        EVENT_QUEUE.append(new_event)

    return f"Event emitted: {event_type}"

@tool(name="Read Events", aliases=["read_events", "get_events"])
def read_events(event_type: Optional[str] = None, **args) -> str:
    """
    Read events from the shared event queue.

    This tool allows agents (especially the orchestrator) to inspect
    pending or historical events in the system.

    Arguments:
    - event_type: optional filter to only return events of a given type

    Returns:
    - str: JSON string of matching events
    """

    if not isinstance(EVENT_QUEUE, list):
        return "Error: EVENT_QUEUE is not initialized properly."

    # Filter events
    filtered = []

    for event in EVENT_QUEUE:
        if not isinstance(event, dict):
            continue

        if event_type is None or event.get("type") == event_type:
            filtered.append(event)

    # Sort by timestamp (oldest first for deterministic processing)
    try:
        filtered.sort(key=lambda x: x.get("timestamp", ""))
    except Exception:
        pass

    # Apply limit
    result = filtered

    return json.dumps(
        {
            "count": len(result),
            "event_type_filter": event_type,
            "events": result,
        },
        indent=2,
        ensure_ascii=False,
    )

@tool(name="Update Task State")
def update_task_state(task_id: str, new_state: str) -> str:
    """
    Update the state of an existing task.

    ARGUMENTS:
    - task_id: ID of the task whose state should be updated
    - new_state: the new state for the task; must be one of:
        { "TODO", "DONE", "BLOCKED" }

    Behavior:
    - Fails if the task does not exist.
    - Fails if new_state is invalid.
    - Updates ONLY the 'state' field of the task.
    - Does NOT modify description, dependencies, or acceptance criteria.
    - Enforces strict state transitions:
        TODO → DONE
        TODO → BLOCKED
        BLOCKED → DONE
        DONE → (immutable; cannot change)
    """
    plan = load_json_plan()

    if task_id not in plan["tasks"]:
        return f"Error: Task '{task_id}' not found."

    if new_state not in ["TODO", "DONE", "BLOCKED"]:
        return f"Error: Invalid state '{new_state}'."

    current_state = plan["tasks"][task_id]["state"]

    # Enforce immutability of DONE tasks
    if current_state == "DONE":
        return "Error: DONE tasks cannot be modified."

    # Enforce allowed transitions
    allowed = {
        "TODO": ["DONE", "BLOCKED"],
        "BLOCKED": ["DONE"]
    }

    if new_state not in allowed.get(current_state, []):
        return (
            f"Error: Invalid transition {current_state} → {new_state}. "
            f"Allowed: {allowed.get(current_state, [])}"
        )

    plan["tasks"][task_id]["state"] = new_state
    save_json_plan(plan)

    return f"Task '{task_id}' state updated to '{new_state}'."

@tool(name="Scaffold Python Module")
def scaffold_python_module(filepath: str, skeleton: str, **args) -> str:
    """
    Create a minimal Python module scaffold.

    This tool is ONLY for initializing structure:
    - imports
    - empty functions/classes
    - TODO markers
    - section headers

    It MUST NOT contain full implementations.

    Arguments:
    - filepath: relative file path inside workspace
    - skeleton: minimal structural code

    Returns:
    - confirmation message
    """

    try:
        target = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    target.parent.mkdir(parents=True, exist_ok=True)

    with open(target, "w", encoding="utf-8") as f:
        f.write(skeleton.strip() + "\n")

    return f"Scaffold created: {filepath}"

@tool(name="Read Python Module")
def read_python_module(filepath: str, **args) -> str:
    """
    Read the current Python file.
    """
    try:
        target = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target.exists():
        return f"File not found: {filepath}"

    return target.read_text(encoding="utf-8")

@tool(name="Append Code Patch")
def append_code_patch(filepath: str, patch: str, **args) -> str:
    """
    Append a small incremental patch to a Python file.

    Constraints:
    - max 1–3 lines
    - must be a localized addition
    - no full functions/classes

    This tool is for incremental construction only.
    """

    try:
        target = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    target.parent.mkdir(parents=True, exist_ok=True)

    existing = target.read_text(encoding="utf-8") if target.exists() else ""

    new_content = (existing.rstrip() + "\n" + patch.strip() + "\n") if existing else patch + "\n"

    target.write_text(new_content, encoding="utf-8")

    return f"Patch appended to {filepath}"

@tool(name="Insert Code Block")
def insert_code_block(filepath: str, anchor: str, code: str, position: str = "after", **args) -> str:
    """
    Insert a small code block relative to a unique anchor.

    Rules:
    - anchor must appear exactly once
    - insertion must be small (1–10 lines max)

    Arguments:
    - filepath: file path
    - anchor: exact string to locate
    - code: small code block
    - position: before/after
    """

    try:
        target = resolve_src_path(filepath)
    except ValueError as exc:
        return str(exc)

    if not target.exists():
        return "File not found."

    content = target.read_text(encoding="utf-8")

    occurrences = content.count(anchor)

    if occurrences == 0:
        return "Anchor not found."
    if occurrences > 1:
        return "Error: ambiguous anchor (multiple matches)."

    if len(code.splitlines()) > 10:
        return "Rejected: insertion too large."

    if position == "before":
        updated = content.replace(anchor, code + "\n" + anchor, 1)
    else:
        updated = content.replace(anchor, anchor + "\n" + code, 1)

    target.write_text(updated, encoding="utf-8")

    return "Code inserted successfully."
def find_anchor(content: str, token: str):
    # 1. exact
    if token in content:
        return token

    # 2. normalized compare
    norm_content = content.replace("\r\n", "\n").replace("\xa0", " ")
    norm_token = token.replace("\r\n", "\n").replace("\xa0", " ")

    if norm_token in norm_content:
        # recover actual substring (best effort)
        start = norm_content.index(norm_token)
        return content[start:start + len(token)]

    # 3. line-based fallback
    for line in content.splitlines():
        if token.strip() in line.strip():
            return line

    return None
@tool(name="Replace In Code")
def replace_in_code(token: str, replacement: str, **args) -> str:
    """
    Perform a tiny, surgical replacement inside the in‑memory code buffer.

    PURPOSE:
    - This tool replaces exactly ONE occurrence of a token inside MEMORY["code"].
    - It is designed for small, localized edits — not large rewrites.
    - It uses robust anchor matching to avoid accidental multi‑matches.

    WHEN TO USE:
    - Refining a TODO marker into a small implementation.
    - Replacing a placeholder with a finalized line.
    - Making a tiny correction to an existing line.
    - Updating a single variable, function name, or short expression.

    WHEN *NOT* TO USE:
    - Writing large blocks of code.
    - Replacing multiple lines at once.
    - Performing full rewrites or restructuring.
    - Inserting long functions, classes, or multi‑line logic.

    CONSTRAINTS:
    - 'replacement' MUST be tiny (normally 1–3 lines).
    - 'replacement' MUST stay under ~500 characters.
    - Only the FIRST matching token is replaced.
    - The token MUST exist in the code buffer, otherwise an error is returned.

    ARGUMENTS:
    - token: The exact substring or placeholder to replace.
    - replacement: The tiny patch that will replace the token.

    RETURNS:
    - A short confirmation message, or "Anchor not found." if no match exists.
    """


    existing = MEMORY.get("code", "")

    anchor = find_anchor(existing, token)

    if not anchor:
        return "Anchor not found."

    updated = existing.replace(anchor, replacement, 1)

    MEMORY["code"] = updated
    return "Replacement successful."
@tool(name="Replace Line In Code")
def replace_line_in_code(line_number: int, new_text: str, **args) -> str:
    """
    Replace the content of the N-th line inside the in-memory code buffer.

    This tool is ONLY for:
    - tiny localized edits
    - TODO refinement
    - surgical line-level corrections

    It MUST NOT be used for:
    - large rewrites
    - full implementations
    - replacing large sections

    Constraints:
    - new_text should normally be 1–3 lines
    - new_text must stay under 500 characters
    - line_number is 1-based
    - if the line does not exist, empty lines will be created until it does

    Arguments:
    - line_number: the 1-based index of the line to replace
    - new_text: the new content for that line

    Returns:
    - confirmation message
    """

    existing = MEMORY.get("code", "")
    lines = existing.split("\n")

    # Ensure the buffer has enough lines
    while len(lines) < line_number:
        lines.append("")

    # Replace the target line
    lines[line_number - 1] = new_text

    updated = "\n".join(lines)
    MEMORY["code"] = updated

    return f"Line {line_number} replaced successfully in code buffer."

@tool(name="Run Code")
def run_memory_code(**args) -> str:
    """
    Execute the code stored in memory.
    Returns:
    - The output of the executed code, or an error message.
    """
    code = MEMORY.get("code", "")
    if not code:
        return "No code to run."

    try:
        return run_code_tool(code)
    except Exception as e:
        return f"Execution error: {e}"

@tool(name="Write Variable")
def write_variable(name:str, value:Any, **args):
    """
    Write a value to a named variable in memory.
    Arguments:
    - name: the variable name (string)
    - value: the value to store (any JSON-serializable type)
    """
    MEMORY[name] = value
    
@tool(name="Read Variable")
def read_variable(name:str, **args) -> Any:
    """
    Read a value from a named variable in memory.
    Arguments:
    - name: the variable name (string)
    """
    return MEMORY.get(name, None)

@tool(name="Create Plan")
def create_plan(**argv) :
    """
    Create a new empty plan stored as a JSON object.
    ARGUMENTS:
    - (none)

    """
    if PLAN_PATH.exists():
        return f"Error: Plan already exists with content {PLAN_PATH.read_text()}"
    empty : dict= {"tasks": {}}
    save_json_plan(empty)
    return "Plan created."

@tool(name="Add Task")
def add_task(
    task_id: str,
    description: str,
    dependencies: list[str],
    acceptance_criteria: str,
    **argv
) -> str:
    """
    Add a new atomic task to the plan with TODO as state.

    ARGUMENTS:
    - task_id: unique identifier for the task
    - description: atomic task description
    - dependencies: list of task IDs this task depends on
    - acceptance_criteria: concrete, testable success condition

    Behavior:
    - Fails if task_id already exists.
    - Inserts a new structured task object.
    """
    plan = load_json_plan()

    if task_id in plan["tasks"]:
        return f"Error: Task '{task_id}' already exists."

    plan["tasks"][task_id] = {
        "description": description,
        "state": "TODO",
        "dependencies": dependencies,
        "acceptance_criteria": acceptance_criteria
    }

    save_json_plan(plan)
    return f"Task '{task_id}' added."

@tool(name="Update Task Field")
def update_task_field(
    task_id: str,
    field: str,
    value
) -> str:
    """
    Update a single field of an existing task.

    ARGUMENTS:
    - task_id: ID of the task to update
    - field: one of {description, state, dependencies, acceptance_criteria}
    - value: new value for the field

    Behavior:
    - Fails if task does not exist.
    - Fails if field is invalid.
    - Updates only the specified field.
    """
    plan = load_json_plan()

    if task_id not in plan["tasks"]:
        return f"Error: Task '{task_id}' not found."

    if field not in ["description", "state", "dependencies", "acceptance_criteria"]:
        return f"Error: Invalid field '{field}'."

    plan["tasks"][task_id][field] = value

    save_json_plan(plan)
    return f"Task '{task_id}' updated."

@tool(name="Delete Task")
def delete_task(task_id: str) -> str:
    """
    Delete a task from the plan.

    ARGUMENTS:
    - task_id: ID of the task to delete

    Behavior:
    - Fails if task does not exist.
    - Removes the task object.
    - Does NOT automatically remove references in dependencies.
    """
    plan = load_json_plan()

    if task_id not in plan["tasks"]:
        return f"Error: Task '{task_id}' not found."

    del plan["tasks"][task_id]

    save_json_plan(plan)
    return f"Task '{task_id}' deleted."
@tool(name="Read Plan")
def read_plan(**argv) -> dict:
    """
    Read and return the full JSON plan.
    
    ARGUMENTS:
    - (none)

    Behavior:
    - Returns the plan as a Python dict.
    - If no plan exists, returns an empty structure.
    """
    return load_json_plan()
@tool(name="Read Task")
def read_task(task_id: str, **argv) -> dict:
    """
    Read and return a single task from the stored plan.

    ARGUMENTS:
    - task_id: Unique identifier of the task to retrieve

    RETURNS:
    - dict: The task object if found
    - dict: Error object if not found or plan is invalid
    """

    try:
        plan = load_json_plan()["tasks"]

        if task_id not in plan:
            return {
                "error": "TASK_NOT_FOUND",
                "task_id": task_id,
                "message": f"No task found with id: {task_id}"
            }

        task = plan[task_id]

        if not isinstance(task, dict):
            return {
                "error": "INVALID_TASK_FORMAT",
                "task_id": task_id,
                "message": "Task exists but is not a valid object."
            }

        return {
            "success": True,
            "task_id": task_id,
            "task": task
        }

    except Exception as e:
        return {
            "error": "READ_TASK_FAILED",
            "task_id": task_id,
            "message": str(e)
        }

@tool(name="Overwrite Plan")
def overwrite_plan(new_plan: dict) -> str:
    """
    Replace the entire plan with a new JSON object.

    ARGUMENTS:
    - new_plan: full JSON structure to replace the existing plan

    Behavior:
    - Overwrites the plan completely.
    - Use only when restructuring is required.
    """
    save_json_plan(new_plan)
    return "Plan overwritten."



# =========================
# STATE SYSTEM CORE TOOLS
# =========================

# @tool(name="Create State System Skeleton")
# def create_state_system_skeleton(sections: list[str]) -> str:
#     """
#     Initialize a new STATE SYSTEM with empty structured sections.

#     Rules for the model:
#     - Only create section headers (no content).
#     - STATE SYSTEM must remain line-based.
#     - Use this tool exactly once per task.
#     - Keep section names short and simple.

#     Arguments:
#     - sections (list[str]): A list of section names. Each name becomes a header
#       followed by an empty indented line. Example: ["Goal", "Steps", "Status"].

#     Returns:
#     - str: Confirmation message or an error if a STATE SYSTEM already exists.
#     """
#     if MEMORY.get("state_system"):
#         return "Error: STATE SYSTEM already exists. Use update tools instead."

#     skeleton = "\n".join(f"{s}:\n    " for s in sections)
#     MEMORY["state_system"] = skeleton
#     return "STATE SYSTEM initialized successfully."


# @tool(name="Reset State System")
# def reset_state_system() -> str:
#     """
#     Clear the current STATE SYSTEM.
#     Use only when restarting a task.

#     Arguments:
#     - None

#     Returns:
#     - str: Confirmation message.
#     """
#     MEMORY["state_system"] = ""
#     return "STATE SYSTEM reset successfully."


# @tool(name="Read State System")
# def read_state_system() -> str:
#     """
#     Read the current STATE SYSTEM.

#     Arguments:
#     - None

#     Returns:
#     - str: The full STATE SYSTEM content, or empty string if none exists.
#     """
#     return MEMORY.get("state_system", "")


# # =========================
# # PRIMARY MUTATION TOOLS (LINE-BASED)
# # =========================

# @tool(name="Replace State Line")
# def replace_state_line(line_number: int, new_text: str) -> str:
#     """
#     Replace a specific line in the STATE SYSTEM.

#     Rules:
#     - line_number is 1-based.
#     - new_text must be short (1–3 lines max).
#     - This tool overwrites the target line exactly.

#     Arguments:
#     - line_number (int): The line index to replace (1 = first line).
#     - new_text (str): The new content to insert at that line.

#     Returns:
#     - str: Confirmation message.
#     """
#     state = MEMORY.get("state_system", "")
#     lines = state.split("\n")

#     while len(lines) < line_number:
#         lines.append("")

#     lines[line_number - 1] = new_text

#     MEMORY["state_system"] = "\n".join(lines)
#     return "STATE SYSTEM updated successfully."


# @tool(name="Insert State Line")
# def insert_state_line(line_number: int, new_text: str, position: str = "before") -> str:
#     """
#     Insert a line into the STATE SYSTEM.

#     Rules:
#     - line_number is 1-based.
#     - position determines insertion relative to the target line.
#     - new_text may contain multiple lines (they will be inserted in order).

#     Arguments:
#     - line_number (int): The reference line number.
#     - new_text (str): The text to insert (can be multi-line).
#     - position (str): "before" or "after" (default: "before").

#     Returns:
#     - str: Confirmation message.
#     """
#     state = MEMORY.get("state_system", "")
#     lines = state.split("\n")

#     while len(lines) < line_number:
#         lines.append("")

#     index = line_number - 1
#     if position == "after":
#         index += 1

#     for line in reversed(new_text.split("\n")):
#         lines.insert(index, line)

#     MEMORY["state_system"] = "\n".join(lines)
#     return "STATE SYSTEM updated successfully."


# @tool(name="Append State")
# def append_state(text: str) -> str:
#     """
#     Append small content to the end of the STATE SYSTEM.

#     Rules:
#     - Text must be short.
#     - Appends exactly at the end, preserving line structure.

#     Arguments:
#     - text (str): The content to append.

#     Returns:
#     - str: Confirmation message.
#     """
#     state = MEMORY.get("state_system", "")
#     MEMORY["state_system"] = (state.rstrip() + "\n" + text) if state else text
#     return "STATE SYSTEM updated successfully."


# @tool(name="Replace In State System")
# def replace_in_state_system(token: str, replacement: str, count: int = 1) -> str:
#     """
#     Replace a token safely inside the STATE SYSTEM.

#     Rules:
#     - Only replaces the first occurrence by default.
#     - Replacement must be short.
#     - Token must exist in the STATE SYSTEM.

#     Arguments:
#     - token (str): The substring to search for.
#     - replacement (str): The text that replaces the token.
#     - count (int): Maximum number of replacements (default: 1).

#     Returns:
#     - str: Confirmation message or "Token not found."
#     """
#     state = MEMORY.get("state_system", "")

#     if token not in state:
#         return "Token not found."

#     MEMORY["state_system"] = state.replace(token, replacement, count)
#     return "STATE SYSTEM updated successfully."


@tool(name="Ask User To Choose", user_input=True)
def ask_user_to_choose(prompt: str, options: list[str], **args) -> str:
    """
    Present N options to the user in a simple shell-style UI and return the user's choice.

    Behavior:
    - Displays the prompt
    - Displays each option with a numeric index (1..N)
    - Asks the user to type the number of their choice
    - Repeats until a valid number is entered
    - Returns the selected option as a string

    Arguments:
    - prompt: A short sentence explaining what the user is choosing.
    - options: A list of option strings (at least 2).

    Returns:
    - The chosen option (string)
    """
    print(prompt.strip())
    print("\nPlease choose one of the following options:\n")

    for i, opt in enumerate(options, start=1):
        print(f"  {i}. {opt}")

    while True:
        choice = input("\nEnter the number of your choice: ").strip()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        print("Invalid choice. Please enter a valid number.")


# -----------------------------
# Product Spec Tools for PO
# -----------------------------

def _spec_folder_for(spec_type: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", spec_type.strip().lower())
    return WORKSPACE_ROOT / "specs" / safe


def _spec_filename_for(title: str) -> str:
    name = title.strip().lower()
    name = re.sub(r"[^a-z0-9_-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return f"{name}.json"

@tool(name="Create Product Spec")
def create_product_spec(
    title: str,
    spec_type: Literal["PRD", "Epic", "Story", "Release", "RFC"],
    objective: str,
    owner: str = "Product",
    priority: str = "Medium",
) -> str:
    """
    Create a new product specification file stored under `workspace/specs/<spec_type>/`.

    Arguments:
    - title (str): Human-friendly title of the spec (will be used to derive filename).
    - spec_type (Literal): One of "PRD", "Epic", "Story", "Release", "RFC".
    - objective (str): High-level objective or summary for this specification.
    - owner (str): Owner or team (default: "Product").
    - priority (str): Priority label (e.g. "High", "Medium", "Low").

    Returns:
    - str: Success or error message.
    """

    allowed = {"PRD", "Epic", "Story", "Release", "RFC"}
    if spec_type not in allowed:
        return f"Error: spec_type must be one of {sorted(list(allowed))}"

    folder = _spec_folder_for(spec_type)
    folder.mkdir(parents=True, exist_ok=True)

    filename = _spec_filename_for(title)
    target = folder / filename
    if target.exists():
        return f"Error: Spec already exit : {spec_type}/{title}"
    spec = {
        "title": title,
        "spec_type": spec_type,
        "objective": objective,
        "owner": owner,
        "priority": priority,
        "user_stories": [],
        "requirements": [],
        "acceptance_criteria": [],
        "ux_flows": [],
        "api_contracts": [],
    }

    try:
        with open(target, "w", encoding="utf-8") as f:
            json.dump(spec, f, indent=2, ensure_ascii=False)
        return f"Spec created: {spec_type}/{title}"
    except Exception as e:
        return f"Error creating spec: {e}"


@tool(name="Read Spec")
def read_spec(spec_type: str, title: str) -> str:
    """
    Read the JSON spec for the given `spec_type` and `title`.

    Arguments:
    - spec_type (str): One of the spec folders (e.g. "PRD", "Epic").
    - title (str): The title used when the spec was created.

    Returns:
    - str: The JSON content of the spec or an error message.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename
    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"
    try:
        return target.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading spec: {e}"

@tool(name="List Specs")
def list_specs(spec_type: str | None = None) -> str:
    """
    List all existing product specifications.

    Arguments:
    - spec_type (optional): If provided, filters by spec type (PRD, Epic, Story, Release, RFC).
      If None, lists all specs across all types.

    Returns:
    - str: JSON-formatted list of available specs.
    """

    try:
        base_path = Path("workspace/specs")

        if not base_path.exists():
            return json.dumps({"specs": []}, indent=2)

        allowed = {"PRD", "Epic", "Story", "Release", "RFC"}

        results = []

        # Case 1: list all
        if spec_type is None:
            folders = [p for p in base_path.iterdir() if p.is_dir()]

        else:
            if spec_type not in allowed:
                return f"Error: spec_type must be one of {sorted(list(allowed))}"

            folders = [_spec_folder_for(spec_type)]

        for folder in folders:
            if not folder.exists():
                continue

            for file in folder.glob("*.json"):
                try:
                    data = json.loads(file.read_text(encoding="utf-8"))

                    results.append({
                        "title": data.get("title"),
                        "spec_type": data.get("spec_type"),
                        "objective": data.get("objective"),
                        "priority": data.get("priority"),
                        "owner": data.get("owner"),
                    })

                except Exception:
                    continue

        return json.dumps({"specs": results}, indent=2)

    except Exception as e:
        return f"Error listing specs: {e}"
@tool(name="Delete Spec")
def delete_spec(spec_type: str, title: str) -> str:
    """
    Delete a specification file.

    Arguments:
    - spec_type (str): Spec folder (e.g. "PRD").
    - title (str): Title of the spec to delete.

    Returns:
    - str: Confirmation message or error.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename
    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"
    try:
        target.unlink()
        return f"Spec deleted: {spec_type}/{title}"
    except Exception as e:
        return f"Error deleting spec: {e}"

@tool(name="Add User Story")
def add_user_story(
    spec_type: str,
    title: str,
    story_title: str,
    persona: str,
    goal: str,
    benefit: str,
    priority: str = "Medium",
) -> str:
    """
    Append or replace a user story inside an existing specification AND
    create/update a dedicated Story spec.

    Behavior:
    - If a story with the same title already exists in the PRD/Epic, it is replaced.
    - Otherwise, the story is appended.
    - A dedicated Story spec (spec_type="Story") is created or overwritten.
      This Story spec contains only the content of the newly added story.

    Arguments:
    - spec_type (str): The type of the parent spec (e.g., "PRD", "Epic").
    - title (str): Title of the parent spec file to append the story to.
    - story_title (str): Title of the Story spec that will be created or replaced.
    - persona (str): Persona associated with the story.
    - goal (str): The user's goal.
    - benefit (str): The benefit or value of achieving the goal.
    - priority (str): Priority label for the story ("High", "Medium", "Low").

    Returns:
    - str: Confirmation message or error message.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename

    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"

    try:
        spec: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))

        # Build story object
        story = {
            "title": story_title,
            "persona": persona,
            "goal": goal,
            "benefit": benefit
        }

        # Ensure user_stories exists
        stories: list = spec.get("user_stories", [])

        # Remove any existing story with the same title
        stories = [s for s in stories if s.get("title") != story_title]

        # Add the new/updated story
        stories.append(story)
        spec["user_stories"] = stories

        # Create or overwrite the Story spec
        creation_result = create_product_spec(
            story_title,
            "Story",
            goal + " | " + benefit,
            persona,
            priority
        )

        if isinstance(creation_result, str) and creation_result.startswith("Error"):
            return creation_result

        # Save updated PRD/Epic
        target.write_text(
            json.dumps(spec, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return "User story added or updated."

    except Exception as e:
        return f"Error adding user story: {e}"



@tool(name="Add Requirement")
def add_requirement(
    spec_type: str,
    title: str,
    requirement_type: Literal[
        "Functional",
        "Non Functional",
        "Security",
        "Performance",
        "Analytics",
        "Compliance",
    ],
    description: str,
) -> str:
    """
    Add a requirement to a spec.

    Arguments:
    - spec_type (str): Spec folder (e.g. "PRD").
    - title (str): Title of the spec file.
    - requirement_type (Literal): One of the listed requirement categories.
    - description (str): Text describing the requirement.

    Returns:
    - str: Confirmation or error message.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename
    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"
    try:
        spec = json.loads(target.read_text(encoding="utf-8"))
        req = {"type": requirement_type, "description": description}
        spec.setdefault("requirements", []).append(req)
        target.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        return "Requirement added."
    except Exception as e:
        return f"Error adding requirement: {e}"


@tool(name="Add Acceptance Criteria")
def add_acceptance_criteria(spec_type: str, title: str, criteria: list[str]) -> str:
    """
    Add one or more acceptance criteria lines to a spec.

    Arguments:
    - spec_type (str): Spec folder (e.g. "PRD").
    - title (str): Title of the spec file.
    - criteria (list[str]): List of short acceptance criteria strings.

    Returns:
    - str: Confirmation or error message.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename
    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"
    try:
        spec = json.loads(target.read_text(encoding="utf-8"))
        spec.setdefault("acceptance_criteria", []).extend(criteria)
        target.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        return "Acceptance criteria added."
    except Exception as e:
        return f"Error adding acceptance criteria: {e}"


@tool(name="Add UX Flow")
def add_ux_flow(spec_type: str, title: str, flow_steps: list[str]) -> str:
    """
    Add a UX flow (ordered steps) to the spec.

    Arguments:
    - spec_type (str): Spec folder (e.g. "Story").
    - title (str): Title of the spec file.
    - flow_steps (list[str]): Ordered list of user-facing steps.

    Returns:
    - str: Confirmation or error message.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename
    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"
    try:
        spec = json.loads(target.read_text(encoding="utf-8"))
        spec.setdefault("ux_flows", []).append({"steps": flow_steps})
        target.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        return "UX flow added."
    except Exception as e:
        return f"Error adding ux flow: {e}"


@tool(name="Add API Contract")
def add_api_contract(
    spec_type: str,
    title: str,
    endpoint: str,
    method: str,
    request_schema: str,
    response_schema: str,
    **argv
) -> str:
    """
    Add a simple API contract to the spec.

    Arguments:
    - spec_type (str): Spec folder (e.g. "PRD").
    - title (str): Title of the spec file.
    - endpoint (str): API endpoint path (e.g. "/v1/checkout").
    - method (str): HTTP method (e.g. "GET", "POST").
    - request_schema (str): Short JSON schema or description for the request.
    - response_schema (str): Short JSON schema or description for the response.

    Returns:
    - str: Confirmation or error message.
    """

    folder = _spec_folder_for(spec_type)
    filename = _spec_filename_for(title)
    target = folder / filename
    if not target.exists():
        return f"Error: Spec not found: {spec_type}/{title}"
    try:
        spec = json.loads(target.read_text(encoding="utf-8"))
        contract = {
            "endpoint": endpoint,
            "method": method,
            "request_schema": request_schema,
            "response_schema": response_schema,
            **argv
        }
        spec.setdefault("api_contracts", []).append(contract)
        target.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        return "API contract added."
    except Exception as e:
        return f"Error adding api contract: {e}"


@tool(name="List Specifications")
def list_specifications(spec_type: str = "") -> str:
    """
    List existing specifications. If `spec_type` is provided, lists specs under that type;
    otherwise lists all spec types and their specs.

    Arguments:
    - spec_type (str): Optional spec folder name (e.g. "PRD"). If empty, list all.

    Returns:
    - str: Human-readable listing of specs or a message when none exist.
    """

    base = WORKSPACE_ROOT / "specs"
    if not base.exists():
        return "No specifications found."

    def titles_in_folder(folder: Path) -> list[str]:
        out = []
        for f in folder.glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
                t = j.get("title") or f.stem
            except Exception:
                t = f.stem
            out.append(t)
        return out

    if spec_type:
        folder = _spec_folder_for(spec_type)
        if not folder.exists():
            return f"No specifications found for type: {spec_type}"
        titles = titles_in_folder(folder)
        if not titles:
            return f"No specifications found for type: {spec_type}"
        return ", ".join(titles)

    # list all types
    lines: list[str] = []
    for child in sorted(base.iterdir()):
        if child.is_dir():
            titles = titles_in_folder(child)
            if titles:
                lines.append(f"{child.name}: {', '.join(titles)}")

    if not lines:
        return "No specifications found."

    return "\n".join(lines)

