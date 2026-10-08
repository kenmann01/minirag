# Internal and Confidential - Not for External Distribution.
"""A jailed repository agent. Tool calls stay inside the repo root."""

from pathlib import Path

from pydantic import BaseModel

from app.config import Settings

TOOL_TURN_CAP = 12


class RunOutput(BaseModel):
    """What one agent attempt returns to the scoreboard."""

    answer: str
    tool_calls: int
    prompt_tokens: int
    completion_tokens: int


def resolve_inside(root: Path, raw: str) -> Path:
    """Resolve a tool path and reject anything outside the repository."""
    root_resolved = root.resolve()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    candidate = candidate.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise PermissionError(raw)
    return candidate


def list_directory(root: Path, raw: str) -> str:
    """List one directory inside the repo, names sorted."""
    path = resolve_inside(root, raw)
    if not path.is_dir():
        return f"not a directory: {raw}"
    return "\n".join(sorted(entry.name for entry in path.iterdir()))


def read_text(root: Path, raw: str, limit: int = 8000) -> str:
    """Read one file inside the repo."""
    path = resolve_inside(root, raw)
    if not path.is_file():
        return f"not a file: {raw}"
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def ollama_base_url(host: str) -> str:
    """Ollama's OpenAI-compatible base URL, with ``/v1`` appended when missing."""
    base = host.rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def build_agent(repo: Path, settings: Settings, on_tool=None):
    """Build the local Ollama agent with list and read tools jailed to ``repo``."""
    from pydantic_ai import Agent
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider

    model = OllamaModel(
        settings.ollama_model,
        provider=OllamaProvider(base_url=ollama_base_url(settings.ollama_host)),
        settings={"temperature": 0, "seed": 77, "thinking": False},
    )
    agent = Agent(
        model,
        instructions=(
            "Answer the task. If the prompt already states the target symbol and its file, "
            "reply with that symbol and file path and do not call tools. "
            "Otherwise use list_dir and read_file. Paths are relative to the repository root. "
            "When the task names a symbol, include that symbol and its file name."
        ),
        output_type=str,
    )

    @agent.tool_plain
    def list_dir(path: str) -> str:
        """List entries in a repository directory.

        Args:
            path: Directory relative to the repository root.
        """
        try:
            found = list_directory(repo, path)
        except PermissionError:
            found = "path is outside the repository"
        if on_tool is not None:
            on_tool(
                {
                    "call": "tool",
                    "title": "list_dir",
                    "detail": path,
                    "ran": path,
                    "returned": found,
                }
            )
        return found

    @agent.tool_plain
    def read_file(path: str) -> str:
        """Read a repository file.

        Args:
            path: File relative to the repository root.
        """
        try:
            found = read_text(repo, path)
        except PermissionError:
            found = "path is outside the repository"
        if on_tool is not None:
            on_tool(
                {
                    "call": "tool",
                    "title": "read_file",
                    "detail": path,
                    "ran": path,
                    "returned": found,
                }
            )
        return found

    return agent


def run_agent(agent, prompt: str) -> RunOutput:
    """Run one prompt. Past the tool-call cap, the attempt fails closed."""
    from pydantic_ai import UsageLimits
    from pydantic_ai.exceptions import UsageLimitExceeded

    try:
        result = agent.run_sync(
            prompt,
            usage_limits=UsageLimits(
                tool_calls_limit=TOOL_TURN_CAP, request_limit=TOOL_TURN_CAP + 1
            ),
        )
    except UsageLimitExceeded:
        return RunOutput(answer="", tool_calls=TOOL_TURN_CAP, prompt_tokens=0, completion_tokens=0)
    usage = result.usage
    answer = result.output if isinstance(result.output, str) else str(result.output)
    return RunOutput(
        answer=answer,
        tool_calls=int(usage.tool_calls),
        prompt_tokens=int(usage.input_tokens),
        completion_tokens=int(usage.output_tokens),
    )
