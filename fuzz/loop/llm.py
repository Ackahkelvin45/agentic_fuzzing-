"""llm.py — provider-agnostic chat client (default: DeepSeek), stdlib only.

DeepSeek exposes an OpenAI-compatible /chat/completions endpoint, so this thin
client works for DeepSeek today and any OpenAI-compatible provider (incl. a
local server) by changing env vars — no code change, no extra dependency
(requirements.txt stays just `hypothesis`).

Config via environment:
    LLM_API_KEY        API key. If unset (or LLM_MOCK=1), runs in MOCK mode:
                       returns a canned strategy so the whole loop runs offline
                       with no network and no spend.
    LLM_BASE_URL       default https://api.deepseek.com
    LLM_MODEL          default deepseek-chat   (or deepseek-reasoner)
    LLM_PRICE_IN       $ per 1M input tokens   (default 0.27, adjust to current)
    LLM_PRICE_OUT      $ per 1M output tokens  (default 1.10, adjust to current)

The key is read from the environment only and never logged or committed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_dotenv(path: Path = _ENV_FILE) -> None:
    """Minimal .env loader (KEY=VALUE lines). Already-exported environment
    variables take precedence, so `export DEEPSEEK_API_KEY=...` overrides the
    file. No dependency, no override of real env."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val

# A canned, genuinely-working recursive JSON strategy used in MOCK mode so the
# loop is demonstrable offline. It mirrors what we ask the model to produce.
MOCK_STRATEGY = '''\
from hypothesis import strategies as st
import json

def _leaf():
    return st.one_of(
        st.integers(), st.booleans(), st.none(),
        st.text(max_size=6),
        st.floats(allow_nan=False, allow_infinity=False),
    )

_json = st.recursive(
    _leaf(),
    lambda kids: st.one_of(
        st.lists(kids, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=4), kids, max_size=4),
    ),
    max_leaves=12,
)

strategy = _json.map(lambda o: json.dumps(o))
'''


@dataclass
class LLMConfig:
    api_key: str | None
    base_url: str
    model: str
    price_in: float
    price_out: float
    mock: bool

    @classmethod
    def from_env(cls) -> "LLMConfig":
        load_dotenv()  # pull .env into os.environ (real env vars still win)
        key = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
        mock = os.environ.get("LLM_MOCK") == "1" or not key
        return cls(
            api_key=key,
            base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            model=os.environ.get("LLM_MODEL", "deepseek-chat"),
            price_in=float(os.environ.get("LLM_PRICE_IN", "0.27")),
            price_out=float(os.environ.get("LLM_PRICE_OUT", "1.10")),
            mock=mock,
        )

    def describe(self) -> str:
        return f"{'MOCK' if self.mock else self.model} @ {self.base_url}"


def chat(messages: list[dict], config: LLMConfig,
         temperature: float = 0.7, timeout: int = 120) -> tuple[str, int, int]:
    """Return (content, prompt_tokens, completion_tokens).

    In MOCK mode returns the canned strategy and zero token cost, so callers
    (the loop) run unchanged with or without a key.
    """
    if config.mock:
        return f"```python\n{MOCK_STRATEGY}```", 0, 0

    body = json.dumps({
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{config.base_url}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"LLM HTTP {e.code}: {detail}") from e

    content = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage", {})
    return content, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
