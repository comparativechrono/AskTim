"""Claude, through Anthropic's Messages API (streamed).

Notes on the API that matter here:
- Current Claude models think before they answer (adaptive thinking, on by default; the
  thinking text is omitted by default). In a tool-use loop the thinking blocks of an
  assistant turn must be sent back unchanged, so a turn keeps its content blocks as received.
- The system prompt and the tools must stay the same during an agent's run (a change would
  break the check the API makes on earlier thinking blocks).
- output_config.effort sets how much work the model does: "low" for chat, "medium" for the agent.
- A top-level cache_control turns on automatic prompt caching (cheaper repeated requests).
Optional parameters that a model or a proxy does not accept are dropped and the request is
sent again.
"""

import json
import re

from .. import net
from . import Provider, ProviderError, ToolCall, Turn, error_message_from_body

# models that accept output_config.effort (Claude 4.5 Opus and later generations)
_EFFORT_MODELS = re.compile(r"claude-(fable|mythos|opus|sonnet|haiku)-(5|[6-9])|claude-opus-4-[5-9]|claude-sonnet-4-6|claude-mythos")


class AnthropicProvider(Provider):
    name = "claude"

    def __init__(self, key, model, cfg):
        Provider.__init__(self, key, model, cfg)
        self.base = (cfg.get("anthropic_base_url") or "https://api.anthropic.com").rstrip("/")
        self.version = cfg.get("anthropic_version") or "2026-10-21"
        self._drop = set()  # optional parameters this service has refused

    # -- the request ------------------------------------------------------------------------

    def _messages(self, conversation):
        msgs = []

        def add(role, blocks):
            if msgs and msgs[-1]["role"] == role:
                msgs[-1]["content"].extend(blocks)
            else:
                msgs.append({"role": role, "content": list(blocks)})

        for it in conversation.items:
            if it["kind"] == "user":
                add("user", [{"type": "text", "text": it["text"] or "(empty)"}])
            elif it["kind"] == "assistant":
                t = it["turn"]
                if t.provider == self.name and isinstance(t.raw, list) and t.raw:
                    add("assistant", t.raw)
                else:
                    blocks = []
                    if t.text:
                        blocks.append({"type": "text", "text": t.text})
                    for c in t.tool_calls:
                        blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input})
                    add("assistant", blocks or [{"type": "text", "text": "(no reply)"}])
            elif it["kind"] == "results":
                blocks = [
                    {"type": "tool_result", "tool_use_id": r.id, "content": r.content or "(no output)", "is_error": bool(r.is_error)}
                    for r in it["results"]
                ]
                if it.get("text"):
                    blocks.append({"type": "text", "text": it["text"]})
                add("user", blocks)
        return msgs

    def _body(self, system, conversation, tools, purpose):
        max_tokens = int((self.cfg.get("max_tokens") or {}).get(purpose) or 16000)
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system}],
            "messages": self._messages(conversation),
            "stream": True,
        }
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        effort = (self.cfg.get("effort") or {}).get(purpose)
        if effort and "output_config" not in self._drop and _EFFORT_MODELS.search(self.model):
            body["output_config"] = {"effort": effort}
        if "cache_control" not in self._drop:
            body["cache_control"] = {"type": "ephemeral"}
        return body

    def _headers(self):
        return {"x-api-key": self.key, "anthropic-version": self.version}

    # -- one call ---------------------------------------------------------------------------

    def complete(self, system, conversation, tools=None, purpose="chat", on_text=None, on_status=None):
        emitted = [False]

        def emit(t):
            emitted[0] = True
            if on_text:
                on_text(t)

        def attempt():
            body = self._body(system, conversation, tools, purpose)
            resp = net.open_request(self.base + "/v1/messages", self._headers(), body, timeout=120)
            return self._read_stream(resp, emit)

        for _round in range(4):
            try:
                return net.with_retries(attempt, attempts=3, on_wait=lambda e, w: on_status and on_status(_wait_note(e, w, emitted[0])))
            except net.HTTPError as e:
                fixed = self._fix_request(e)
                if fixed and not emitted[0]:
                    continue
                raise self._error(e)
        raise ProviderError("The request to Claude could not be made to work.", kind="bad_request")

    def _fix_request(self, e):
        """For a 400 about an optional parameter: leave it out next time (True if changed)."""
        if e.status != 400:
            return False
        msg = error_message_from_body(e.body).lower()
        if "anthropic-version" in msg and self.version != "2023-06-01":
            self.version = "2023-06-01"
            return True
        for param, words in (("output_config", ("output_config", "effort")), ("cache_control", ("cache_control",))):
            if param not in self._drop and any(w in msg for w in words):
                self._drop.add(param)
                return True
        return False

    def _read_stream(self, resp, emit):
        blocks = {}
        partial = {}
        stop_reason, usage, model = None, {}, self.model
        for event, data in net.iter_sse(resp):
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            typ = ev.get("type") or event
            if typ == "message_start":
                m = ev.get("message") or {}
                model = m.get("model") or model
                usage.update(m.get("usage") or {})
            elif typ == "content_block_start":
                i = ev.get("index", len(blocks))
                b = dict(ev.get("content_block") or {})
                if b.get("type") == "tool_use":
                    b["input"] = {}
                    partial[i] = ""
                blocks[i] = b
            elif typ == "content_block_delta":
                i = ev.get("index", 0)
                d = ev.get("delta") or {}
                b = blocks.setdefault(i, {"type": "text", "text": ""})
                dt = d.get("type")
                if dt == "text_delta":
                    b["text"] = b.get("text", "") + d.get("text", "")
                    emit(d.get("text", ""))
                elif dt == "input_json_delta":
                    partial[i] = partial.get(i, "") + d.get("partial_json", "")
                elif dt == "thinking_delta":
                    b["thinking"] = b.get("thinking", "") + d.get("thinking", "")
                elif dt == "signature_delta":
                    b["signature"] = d.get("signature", "")
                elif dt == "citations_delta":
                    b.setdefault("citations", []).append(d.get("citation"))
            elif typ == "content_block_stop":
                i = ev.get("index", 0)
                if i in partial:
                    raw = partial.pop(i).strip()
                    try:
                        blocks[i]["input"] = json.loads(raw) if raw else {}
                    except ValueError:
                        blocks[i]["input"] = {"_incomplete": raw[:2000]}
            elif typ == "message_delta":
                d = ev.get("delta") or {}
                stop_reason = d.get("stop_reason") or stop_reason
                usage.update(ev.get("usage") or {})
            elif typ == "error":
                err = ev.get("error") or {}
                etype = err.get("type", "")
                status = 529 if etype == "overloaded_error" else 429 if etype == "rate_limit_error" else 500 if etype == "api_error" else 400
                raise net.HTTPError(err.get("message") or etype or "error", status=status, body=json.dumps(ev))
            # ping and new event types are ignored
        # a tool call whose input never finished (the reply was cut off)
        for i, raw in partial.items():
            blocks[i]["input"] = {"_incomplete": raw[:2000]}
        content = [blocks[i] for i in sorted(blocks)]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        calls = [ToolCall(b.get("id"), b.get("name"), b.get("input")) for b in content if b.get("type") == "tool_use"]
        sr = {"end_turn": "end", "tool_use": "tool_use", "max_tokens": "max_tokens", "stop_sequence": "end", "refusal": "refusal"}.get(stop_reason or "end_turn", stop_reason or "end")
        if stop_reason == "model_context_window_exceeded":
            sr = "max_tokens"
        if stop_reason is None and not content:
            raise net.HTTPError("The answer from Claude was empty or broke off.", network=True)
        return Turn(text=text, tool_calls=calls, stop_reason=sr, model=model, provider=self.name, raw=content, usage=usage)

    # -- errors -----------------------------------------------------------------------------

    def _error(self, e):
        msg = error_message_from_body(e.body) if e.body else str(e)
        if e.network:
            return ProviderError("Could not reach Claude: %s Check your internet connection and try again." % e, kind="network")
        if e.status == 401:
            return ProviderError("Your Claude API key was not accepted (%s). Check it, or store a new one with:  AskTim! Claude" % msg, kind="auth", status=401)
        if e.status == 403:
            return ProviderError("Your key may not use this model or service (%s)." % msg, kind="auth", status=403)
        if e.status == 404:
            return ProviderError(
                "The model %r was not found (%s). Choose a current one, for example:  AskTim! model claude-sonnet-5-5" % (self.model, msg),
                kind="notfound",
                status=404,
            )
        if e.status == 400 and "credit" in msg.lower():
            return ProviderError("Your Anthropic account has no credit left (%s). Add credit at console.anthropic.com, or use another key." % msg, kind="credit", status=400)
        if e.status == 429:
            return ProviderError("Too many requests for your key just now (%s). Wait a minute and try again." % msg, kind="rate", status=429)
        if e.status in (500, 502, 503, 504, 529):
            return ProviderError("Claude is busy or having trouble (HTTP %s: %s). This is not your fault: try again in a minute." % (e.status, msg), kind="busy", status=e.status)
        if e.status == 413:
            return ProviderError("The request was too large for the service (%s)." % msg, kind="bad_request", status=413)
        return ProviderError("Claude refused the request (HTTP %s: %s)." % (e.status, msg), kind="bad_request", status=e.status, detail=e.body)


def _wait_note(e, wait, emitted):
    what = "Too many requests" if e.status == 429 else "The service is busy" if e.status else "No answer from the service"
    again = " – the answer broke off" if emitted else ""
    return "%s%s; waiting %d s and asking again…" % (what, again, round(wait))
