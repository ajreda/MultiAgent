import os
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional


type LMStudioMessageRole = Literal["system", "user", "assistant", "tool", "logs"]


@dataclass
class LMStudioMessage:
    role: LMStudioMessageRole
    content: str

@dataclass
class LMStudioClient:
    model: Optional[str] = None
    base_url: str = "http://127.0.0.1:1234"
    generate_path: str = "/v1/chat/completions"
    
    def __init__(
        self,
        model: str | None = None,
        base_url: str = "http://127.0.0.1:1234",
        generate_path: str = "/v1/chat/completions",
        default_max_tokens: int | None = None,
    ):
        self.model = model or os.getenv("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
        self.base_url = base_url.rstrip("/")
        self.generate_path = generate_path
        env_max_tokens = os.getenv("LM_STUDIO_MAX_TOKENS")
        if default_max_tokens is None and env_max_tokens is not None:
            default_max_tokens = int(env_max_tokens)
        self.default_max_tokens = default_max_tokens

    @staticmethod
    def _serialize_message(message: LMStudioMessage | dict[str, Any]) -> dict[str, Any]:
        if isinstance(message, dict):
            return message
        if hasattr(message, "__dataclass_fields__"):
            return asdict(message)
        return {"role": "user", "content": str(message)}

    def generate(self, messages: list[LMStudioMessage], max_tokens: int | None = None) -> str:
        import requests

        url = f"{self.base_url}{self.generate_path}"

        effective_max_tokens: Any = self.default_max_tokens if max_tokens is None else max_tokens
        payload = {
            "model": self.model,
            "messages": [self._serialize_message(message) for message in messages],
        }
        if effective_max_tokens is not None:
            payload["max_tokens"] = effective_max_tokens

        try:
            resp = requests.post(url, json=payload, timeout=512)
            resp.raise_for_status()

            data = resp.json()

            if "choices" in data and data["choices"]:
                choice = data["choices"][0]
                if isinstance(choice, dict):
                    message = choice.get("message") or {}
                    if isinstance(message, dict):
                        content = message.get("content")
                        if content is not None:
                            return str(content)
                    text = choice.get("text")
                    if text is not None:
                        return str(text)

            if isinstance(data, dict):
                for key in ("response", "text", "result", "output"):
                    value = data.get(key)
                    if value is not None:
                        return str(value)

            return str(data)

        except Exception as e:
            return f"LM Studio request error: {e}"
        