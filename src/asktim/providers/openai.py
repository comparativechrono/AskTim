"""Any service that speaks OpenAI's Chat Completions API (streamed), with function calling.

That covers OpenAI itself and many others - a university's AI gateway, vLLM, Ollama or
LM Studio on a lab server (set the address with `AskTim! OpenAI --url http://host:port/v1`).
A local server may not need a key.
"""

import json

from .. import net
from . import Provider, ProviderError, ToolCall, Turn, error_message_from_body


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, key, model, cfg):
        Provider.__init__(self, key, model, cfg)
        self.base = (cfg.get("openai_base_url") or "https://api.openai.com/v1").rstrip("/")
        self._token_param = "max_completion_tokens" if "api.openai.com" in self.base else None
        self._no_tools = False

    def _messages(self, system, conversation):
        msgs = [{"role": "system", "content": system}]
        for it in conversation.items:
            if it["kind"] == "user":
                msgs.append({"role": "user", "content": it["text"] or "(empty)"})
            elif it["kind"] == "assistant":
                t = it["turn"]
                if t.provider == self.name and isinstance(t.raw, dict):
                    msgs.append(t.raw)
                else:
                    m = {"role": "assistant", "content": t.text or ""}
                    if t.tool_calls:
                        m["tool_calls"] = [
                            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.input)}} for c in t.tool_calls
                        ]
                    msgs.append(m)
            elif it["kind"] == "results":
                for r in it["results"]:
                    content = r.content or "(no output)"
                    if r.is_error:
                        content = "ERROR: " + content
                    msgs.append({"role": "tool", "tool_call_id": r.id, "content": content})
                if it.get("text"):
                    msgs.append({"role": "user", "content": it["text"]})
        return msgs

    def complete(self, system, conversation, tools=None, purpose="chat", on_text=None, on_status=None):
        def attempt():
            body = {"model": self.model, "messages": self._messages(system, conversation), "stream": True}
            if tools and not self._no_tools:
                body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}} for t in tools]
            mt = self.cfg.get("openai_max_tokens")
            if mt and self._token_param:
                body[self._token_param] = int(mt)
            headers = {"authorization": "Bearer " + self.key} if self.key else {}
            resp = net.open_request(self.base + "/chat/completions", headers, body, timeout=120)
            return self._read(resp, on_text)

        for _round in range(3):
            try:
                return net.with_retries(attempt, attempts=3, on_wait=lambda e, w: on_status and on_status("The service is busy; waiting %d s…" % w))
            except net.HTTPError as e:
                msg = error_message_from_body(e.body).lower()
                if e.status == 400 and self._token_param and ("max_completion_tokens" in msg or "max_tokens" in msg):
                    self._token_param = "max_tokens" if self._token_param == "max_completion_tokens" else None
                    continue
                raise self._error(e)
        raise ProviderError("The request could not be made to work with this service.", kind="bad_request")

    def _read(self, resp, on_text):
        text, calls, finish, model, usage = [], {}, None, self.model, {}
        for _event, data in net.iter_sse(resp):
            if data.strip() == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if ev.get("error"):
                raise net.HTTPError(str(ev["error"].get("message") if isinstance(ev["error"], dict) else ev["error"]), status=500, body=data)
            model = ev.get("model") or model
            if ev.get("usage"):
                usage = ev["usage"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    text.append(d["content"])
                    if on_text:
                        on_text(d["content"])
                for tc in d.get("tool_calls") or []:
                    i = tc.get("index", 0)
                    c = calls.setdefault(i, {"id": None, "name": "", "arguments": ""})
                    if tc.get("id"):
                        c["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        c["name"] += fn["name"]
                    if fn.get("arguments"):
                        c["arguments"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
        out_calls, raw_calls = [], []
        for i in sorted(calls):
            c = calls[i]
            cid = c["id"] or "call_%d" % i
            try:
                args = json.loads(c["arguments"]) if c["arguments"].strip() else {}
            except ValueError:
                args = {"_incomplete": c["arguments"][:2000]}
            out_calls.append(ToolCall(cid, c["name"], args))
            raw_calls.append({"id": cid, "type": "function", "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}})
        raw = {"role": "assistant", "content": "".join(text)}
        if raw_calls:
            raw["tool_calls"] = raw_calls
        sr = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens", "content_filter": "refusal"}.get(finish or "stop", "end")
        if out_calls and sr == "end":
            sr = "tool_use"
        return Turn(text="".join(text), tool_calls=out_calls, stop_reason=sr, model=model, provider=self.name, raw=raw, usage=usage)

    def _error(self, e):
        msg = error_message_from_body(e.body) if e.body else str(e)
        host = self.base.split("/")[2] if "//" in self.base else self.base
        if e.network:
            return ProviderError("Could not reach %s: %s" % (host, e), kind="network")
        if e.status in (401, 403):
            return ProviderError("Your key was not accepted by %s (%s). Store a new one with:  AskTim! OpenAI" % (host, msg), kind="auth", status=e.status)
        if e.status == 404:
            return ProviderError("%s did not find the model %r or the address (%s). Check with:  AskTim! status" % (host, self.model, msg), kind="notfound", status=404)
        if e.status == 429:
            return ProviderError("Too many requests, or no credit left, at %s (%s)." % (host, msg), kind="rate", status=429)
        if e.status and e.status >= 500:
            return ProviderError("%s is busy or having trouble (HTTP %s: %s). Try again in a minute." % (host, e.status, msg), kind="busy", status=e.status)
        return ProviderError("%s refused the request (HTTP %s: %s)." % (host, e.status, msg), kind="bad_request", status=e.status)
