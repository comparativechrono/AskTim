"""Google Gemini, through the Gemini API's streamGenerateContent (server-sent events).

Gemini's free tier allows few requests per model per minute and per day, and its newest
models are sometimes busy. As in the practicals, when the chosen model is busy, over a limit
or not found, the next model of `gemini_fallback_models` is asked, and the answer says which
model replied. The parts of each reply are kept as they came, so that thought signatures go
back to the service unchanged in a function-calling loop.
"""

import json
import time

from .. import net
from . import Provider, ProviderError, ToolCall, Turn, error_message_from_body

_REST = {}  # model -> (until timestamp, why), for this process


def _gemini_schema(schema):
    """Gemini's schemas are OpenAPI-style: upper-case type names, no additionalProperties."""
    if isinstance(schema, dict):
        out = {}
        for k, v in schema.items():
            if k in ("additionalProperties", "$schema", "default"):
                continue
            if k == "type" and isinstance(v, str):
                out[k] = v.upper()
            else:
                out[k] = _gemini_schema(v)
        return out
    if isinstance(schema, list):
        return [_gemini_schema(x) for x in schema]
    return schema


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, key, model, cfg):
        Provider.__init__(self, key, model, cfg)
        self.base = (cfg.get("gemini_base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
        self.fallbacks = [m for m in (cfg.get("gemini_fallback_models") or []) if m and m != model]

    def _contents(self, conversation):
        contents = []

        def add(role, parts):
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": list(parts)})

        for it in conversation.items:
            if it["kind"] == "user":
                add("user", [{"text": it["text"] or "(empty)"}])
            elif it["kind"] == "assistant":
                t = it["turn"]
                if t.provider == self.name and isinstance(t.raw, list) and t.raw:
                    add("model", t.raw)
                else:
                    parts = [{"text": t.text}] if t.text else []
                    for c in t.tool_calls:
                        parts.append({"functionCall": {"name": c.name, "args": c.input}})
                    add("model", parts or [{"text": "(no reply)"}])
            elif it["kind"] == "results":
                parts = []
                for r in it["results"]:
                    fr = {"name": r.name, "response": {"error" if r.is_error else "result": r.content or "(no output)"}}
                    if r.id and not str(r.id).startswith("gemini-"):
                        fr["id"] = r.id
                    parts.append({"functionResponse": fr})
                if it.get("text"):
                    parts.append({"text": it["text"]})
                add("user", parts)
        return contents

    def _body(self, system, conversation, tools, purpose):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self._contents(conversation),
            "generationConfig": {"maxOutputTokens": int((self.cfg.get("max_tokens") or {}).get(purpose) or 16000)},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [{"name": t.name, "description": t.description, "parameters": _gemini_schema(t.parameters)} for t in tools]}]
        return body

    def complete(self, system, conversation, tools=None, purpose="chat", on_text=None, on_status=None):
        body = self._body(system, conversation, tools, purpose)
        models = [self.model] + self.fallbacks
        now = time.time()
        ready = [m for m in models if _REST.get(m, (0, ""))[0] <= now]
        if not ready:  # every model is resting: try the one that rests the shortest time
            ready = sorted(models, key=lambda m: _REST.get(m, (0, ""))[0])[:1]
        told = []
        last = None
        for i, model in enumerate(ready):
            try:
                turn = self._one(model, body, on_text)
                if told:
                    turn.model = "%s (%s)" % (model, "; ".join(told))
                return turn
            except net.HTTPError as e:
                last = e
                why = self._rest(model, e)
                if why is None:  # an error that another model would not avoid
                    raise self._error(e, model)
                told.append("%s %s" % (model, why))
                if on_status and i + 1 < len(ready):
                    on_status("%s %s – asking %s…" % (model, why, ready[i + 1]))
        # every model failed: wait once if a limit per minute or a busy server is the cause
        if last is not None and last.retryable and (last.status in (429, 500, 502, 503, 504) or last.network):
            wait = min(60.0, last.retry_after or 20.0)
            if on_status:
                on_status("No Gemini model could answer just now; waiting %d s and asking again…" % wait)
            time.sleep(wait)
            try:
                return self._one(ready[0], body, on_text)
            except net.HTTPError as e:
                raise self._error(e, ready[0], told)
        raise self._error(last, ready[-1], told)

    def _rest(self, model, e):
        """Note why a model could not answer, and for how long to leave it alone; None: do not try others."""
        msg = error_message_from_body(e.body).lower() if e.body else ""
        now = time.time()
        if e.network:
            _REST[model] = (now + 60, "gave no answer")
            return "gave no answer"
        if e.status == 404:
            _REST[model] = (now + 10 ** 9, "was not found")
            return "was not found"
        if e.status == 429:
            daily = "perday" in msg.replace(" ", "").replace("_", "") or "per day" in msg or "daily" in msg
            if daily:
                _REST[model] = (now + 6 * 3600, "is over its limit for today")
                return "is over its limit for today"
            _REST[model] = (now + (e.retry_after or 60), "is over its limit per minute")
            return "is over its limit per minute"
        if e.status in (500, 502, 503, 504):
            _REST[model] = (now + 120, "is busy")
            return "is busy"
        return None

    def _one(self, model, body, on_text):
        url = "%s/v1beta/models/%s:streamGenerateContent?alt=sse" % (self.base, model)
        resp = net.open_request(url, {"x-goog-api-key": self.key}, body, timeout=120)
        parts, finish, usage, blocked = [], None, {}, None
        for _event, data in net.iter_sse(resp):
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get("error"):
                err = ev["error"]
                raise net.HTTPError(err.get("message", "error"), status=err.get("code") or 500, body=data)
            if (ev.get("promptFeedback") or {}).get("blockReason"):
                blocked = ev["promptFeedback"]["blockReason"]
            usage = ev.get("usageMetadata") or usage
            for cand in ev.get("candidates") or []:
                for p in (cand.get("content") or {}).get("parts") or []:
                    parts.append(p)
                    if "text" in p and not p.get("thought") and on_text and p["text"]:
                        on_text(p["text"])
                if cand.get("finishReason"):
                    finish = cand["finishReason"]
        raw = _merge_parts(parts)
        text = "".join(p.get("text", "") for p in raw if "text" in p and not p.get("thought"))
        calls = []
        n = 0
        for p in raw:
            fc = p.get("functionCall")
            if fc:
                n += 1
                calls.append(ToolCall(fc.get("id") or "gemini-%d" % n, fc.get("name"), fc.get("args") or {}))
        if blocked and not raw:
            return Turn(text="(Gemini did not answer: %s)" % blocked, stop_reason="refusal", model=model, provider=self.name, raw=[{"text": ""}])
        sr = "tool_use" if calls else {"STOP": "end", "MAX_TOKENS": "max_tokens", "SAFETY": "refusal", "RECITATION": "refusal", "MALFORMED_FUNCTION_CALL": "malformed"}.get(finish or "STOP", "end")
        if not raw:
            raw = [{"text": ""}]
        return Turn(text=text, tool_calls=calls, stop_reason=sr, model=model, provider=self.name, raw=raw, usage=usage)

    def _error(self, e, model, told=None):
        msg = error_message_from_body(e.body) if e is not None and e.body else str(e)
        also = (" (%s)" % "; ".join(told)) if told else ""
        if e is None:
            return ProviderError("No Gemini model could answer%s." % also, kind="busy")
        if e.network:
            return ProviderError("Could not reach the Gemini API: %s%s" % (e, also), kind="network")
        if e.status in (400, 401, 403) and ("api key" in msg.lower() or "api_key" in msg.lower() or e.status in (401, 403)):
            return ProviderError("Your Gemini key was not accepted (%s). Store a new one with:  AskTim! Gemini" % msg, kind="auth", status=e.status)
        if e.status == 404:
            return ProviderError("The Gemini model %r was not found%s. Choose a current one with:  AskTim! model NAME" % (model, also), kind="notfound", status=404)
        if e.status == 429:
            return ProviderError(
                "Gemini says there are too many requests%s: %s The free tier allows only a few requests per minute and per day; wait, or use another key." % (also, msg),
                kind="rate",
                status=429,
            )
        if e.status and e.status >= 500:
            return ProviderError("Gemini is busy (HTTP %s)%s. That is on Google's side: try again in a minute." % (e.status, also), kind="busy", status=e.status)
        return ProviderError("Gemini refused the request (HTTP %s: %s)." % (e.status, msg), kind="bad_request", status=e.status)


def _merge_parts(parts):
    """Join the text pieces of a streamed reply; keep function calls and signatures as they came."""
    out = []
    for p in parts:
        if "text" in p and "functionCall" not in p and out and "text" in out[-1] and "functionCall" not in out[-1] and bool(p.get("thought")) == bool(out[-1].get("thought")):
            prev = out[-1]
            prev["text"] = prev.get("text", "") + p.get("text", "")
            if p.get("thoughtSignature"):
                prev["thoughtSignature"] = p["thoughtSignature"]
        else:
            out.append(dict(p))
    return out
