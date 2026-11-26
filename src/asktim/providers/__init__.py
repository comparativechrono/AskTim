"""The AI services AskTim! can talk to, behind one small interface.

A conversation is kept in a neutral form (Conversation); each provider turns it into the
format of its service. An assistant turn keeps the provider's own content ("raw") so that it
can be sent back exactly as it came - Claude needs its thinking blocks back unchanged in a
tool-use loop, Gemini its thought signatures.
"""

import json

from .. import config as _config

__all__ = ["Conversation", "Turn", "ToolCall", "ToolResult", "ToolSpec", "ProviderError", "make_provider"]


class ProviderError(Exception):
    """A problem with the AI service, with a message a student can act on.

    kind: auth | credit | rate | busy | notfound | bad_request | network | refusal | config | other
    """

    def __init__(self, message, kind="other", status=None, detail=None):
        Exception.__init__(self, message)
        self.kind = kind
        self.status = status
        self.detail = detail


class ToolSpec(object):
    def __init__(self, name, description, parameters):
        self.name = name
        self.description = description
        self.parameters = parameters  # a JSON schema: {"type": "object", "properties": {...}, "required": [...]}


class ToolCall(object):
    def __init__(self, id, name, input):
        self.id = id
        self.name = name
        self.input = input if isinstance(input, dict) else {}

    def __repr__(self):
        return "ToolCall(%r, %r, %s)" % (self.id, self.name, json.dumps(self.input)[:200])


class ToolResult(object):
    def __init__(self, id, name, content, is_error=False):
        self.id = id
        self.name = name
        self.content = content
        self.is_error = is_error


class Turn(object):
    """One reply of the model."""

    def __init__(self, text="", tool_calls=None, stop_reason="end", model="", provider="", raw=None, usage=None):
        self.text = text
        self.tool_calls = tool_calls or []
        self.stop_reason = stop_reason  # end | tool_use | max_tokens | refusal | other
        self.model = model
        self.provider = provider
        self.raw = raw
        self.usage = usage or {}


class Conversation(object):
    """The messages of a chat or of an agent's run, in a form that every provider can use."""

    def __init__(self):
        self.items = []

    def user(self, text):
        self.items.append({"kind": "user", "text": text})

    def assistant(self, turn):
        self.items.append({"kind": "assistant", "turn": turn})

    def results(self, results, text=None):
        self.items.append({"kind": "results", "results": list(results), "text": text})

    def __len__(self):
        return len(self.items)

    def to_json(self):
        """Text-only form, for keeping a chat between commands (no thinking, no tool calls)."""
        out = []
        for it in self.items:
            if it["kind"] == "user":
                out.append({"role": "user", "text": it["text"]})
            elif it["kind"] == "assistant" and it["turn"].text and not it["turn"].tool_calls:
                out.append({"role": "assistant", "text": it["turn"].text})
        return out

    @classmethod
    def from_json(cls, items):
        conv = cls()
        for it in items or []:
            if it.get("role") == "user":
                conv.user(it.get("text", ""))
            elif it.get("role") == "assistant":
                conv.assistant(Turn(text=it.get("text", "")))
        return conv


class Provider(object):
    name = "base"

    def __init__(self, key, model, cfg):
        self.key = key
        self.model = model
        self.cfg = cfg

    def complete(self, system, conversation, tools=None, purpose="chat", on_text=None, on_status=None):
        raise NotImplementedError

    def describe(self):
        return "%s · %s" % (_config.PROVIDER_NAMES.get(self.name, self.name), self.model)


def error_message_from_body(body):
    """The service's own error message, from a JSON error body."""
    try:
        data = json.loads(body or "")
    except ValueError:
        return (body or "").strip()[:300]
    if isinstance(data, list) and data:
        data = data[0]
    err = data.get("error", data) if isinstance(data, dict) else data
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)[:500]
    return str(err)[:500]


def make_provider(cfg, provider=None, model=None, key=None):
    """The provider to use, with its key. Raises ProviderError(kind="config") if there is no key."""
    name = _config.normalise_provider(provider) or cfg.provider
    if key is None:
        key, _where = _config.find_key(name)
    model = model or cfg.model(name)
    if name == "openai":
        from .openai import OpenAIProvider

        base = cfg.get("openai_base_url")
        local = any(h in base for h in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))
        if not key and not local:
            raise ProviderError(_no_key_message(name), kind="config")
        if not model:
            raise ProviderError("No model is set for the OpenAI-compatible service. Set one with:  AskTim! model NAME", kind="config")
        return OpenAIProvider(key, model, cfg)
    if not key:
        raise ProviderError(_no_key_message(name), kind="config")
    if name == "gemini":
        from .gemini import GeminiProvider

        return GeminiProvider(key, model, cfg)
    from .anthropic import AnthropicProvider

    return AnthropicProvider(key, model, cfg)


def _no_key_message(name):
    label = {"claude": "Claude", "gemini": "Gemini", "openai": "OpenAI"}[name]
    where = {
        "claude": "console.anthropic.com (Settings → API keys)",
        "gemini": "aistudio.google.com/apikey",
        "openai": "your service's website",
    }[name]
    return (
        "AskTim! has no API key for %s yet. Get one from %s, then run:\n\n    AskTim! %s\n\n"
        "and paste the key when you are asked (it is stored on this computer, readable by you only)."
        % (label, where, label)
    )
