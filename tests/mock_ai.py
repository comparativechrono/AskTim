"""A stand-in for the AI services, for tests: it replays prepared replies and records requests.

It speaks the streaming formats of Anthropic's Messages API (with thinking blocks and
signatures), OpenAI's Chat Completions API and Gemini's streamGenerateContent, so that the
whole of AskTim! - requests, streaming, tool calls, replaying thinking - can be tested
without a key or a network. Like the stand-ins of the practicals, it tests AskTim!, not a model.

    srv = MockAI()                      # starts on a free port
    srv.queue = [reply, reply, ...]     # each reply: {"text": "...", "tools": [(name, input), ...]}
    ... run AskTim! with ASKTIM_ANTHROPIC_URL=srv.url ...
    srv.requests                        # the JSON bodies that were sent
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class MockAI(object):
    def __init__(self, port=0):
        self.queue = []
        self.requests = []
        self.headers = []
        self.fail_next = []  # HTTP statuses to answer with first (e.g. [529] for "overloaded")
        self.default = {"text": "OK."}
        # what GET .../models lists for each service (newest first)
        self.models = {
            "claude": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-1"],
            "gemini": ["gemini-3.5-flash", "gemini-3.8-flash-preview-11-2026", "gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.8-pro", "text-embedding-005"],
            "openai": ["gpt-5-mini", "gpt-5"],
        }
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                # the lists of models (Anthropic and OpenAI: /v1/models; Gemini: /v1beta/models)
                mock.requests.append({"path": self.path, "body": None, "method": "GET"})
                mock.headers.append(dict(self.headers))
                if mock.fail_next:
                    status = mock.fail_next.pop(0)
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "mock error %d" % status}}).encode())
                    return
                if self.path.startswith("/v1beta/models"):
                    data = {"models": [{"name": "models/" + m, "displayName": m.replace("-", " ").title(),
                                        "supportedGenerationMethods": ["embedContent"] if "embedding" in m else ["generateContent", "countTokens"]}
                                       for m in mock.models["gemini"]]}
                elif self.path.startswith("/v1/models") and self.headers.get("x-api-key"):
                    ids = mock.models["claude"]
                    data = {"data": [{"type": "model", "id": m, "display_name": m.replace("claude-", "Claude ").replace("-", " ").title(),
                                      "created_at": "2026-%02d-01T00:00:00Z" % (12 - i)} for i, m in enumerate(ids)], "has_more": False}
                elif self.path.startswith("/v1/models"):
                    data = {"object": "list", "data": [{"id": m, "object": "model", "created": 1800000000 - i, "owned_by": "test"} for i, m in enumerate(mock.models["openai"])]}
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                raw = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                mock.requests.append({"path": self.path, "body": body})
                mock.headers.append(dict(self.headers))
                if mock.fail_next:
                    status = mock.fail_next.pop(0)
                    msg = json.dumps({"type": "error", "error": {"type": "overloaded_error" if status == 529 else "invalid_request_error", "message": "mock error %d" % status}}).encode()
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.send_header("retry-after", "1")
                    self.end_headers()
                    self.wfile.write(msg)
                    return
                reply = mock.queue.pop(0) if mock.queue else mock.default
                if callable(reply):
                    reply = reply(body)
                if "/v1/messages" in self.path:
                    return self._anthropic(reply, body)
                if "chat/completions" in self.path:
                    return self._openai(reply, body)
                if "GenerateContent" in self.path:
                    return self._gemini(reply, body)
                self.send_response(404)
                self.end_headers()

            def _sse(self):
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()

            def _ev(self, event, data):
                if event:
                    self.wfile.write(("event: %s\n" % event).encode())
                self.wfile.write(("data: %s\n\n" % json.dumps(data)).encode())
                self.wfile.flush()

            def _anthropic(self, reply, body):
                self._sse()
                model = body.get("model", "claude-test")
                self._ev("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [], "usage": {"input_tokens": 10, "output_tokens": 1}}})
                i = 0
                # a thinking block, as current models send by default (text omitted, signature present)
                self._ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
                self._ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": ""}})
                self._ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "signature_delta", "signature": "sig-%d" % len(mock.requests)}})
                self._ev("content_block_stop", {"type": "content_block_stop", "index": i})
                i += 1
                self._ev("ping", {"type": "ping"})
                if reply.get("text"):
                    self._ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
                    t = reply["text"]
                    for k in range(0, len(t), 7):
                        self._ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": t[k:k + 7]}})
                    self._ev("content_block_stop", {"type": "content_block_stop", "index": i})
                    i += 1
                for j, (name, inp) in enumerate(reply.get("tools", [])):
                    self._ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": "toolu_%d_%d" % (len(mock.requests), j), "name": name, "input": {}}})
                    s = json.dumps(inp)
                    self._ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": ""}})
                    for k in range(0, len(s), 11):
                        self._ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": s[k:k + 11]}})
                    self._ev("content_block_stop", {"type": "content_block_stop", "index": i})
                    i += 1
                self._ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use" if reply.get("tools") else reply.get("stop", "end_turn")}, "usage": {"output_tokens": 42}})
                self._ev("message_stop", {"type": "message_stop"})

            def _openai(self, reply, body):
                self._sse()
                model = body.get("model", "gpt-test")
                t = reply.get("text") or ""
                for k in range(0, len(t), 5):
                    self._ev(None, {"model": model, "choices": [{"index": 0, "delta": {"content": t[k:k + 5]}}]})
                for j, (name, inp) in enumerate(reply.get("tools", [])):
                    s = json.dumps(inp)
                    self._ev(None, {"model": model, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": j, "id": "call_%d" % j, "type": "function", "function": {"name": name, "arguments": ""}}]}}]})
                    for k in range(0, len(s), 9):
                        self._ev(None, {"model": model, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": j, "function": {"arguments": s[k:k + 9]}}]}}]})
                self._ev(None, {"model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if reply.get("tools") else "stop"}]})
                self.wfile.write(b"data: [DONE]\n\n")

            def _gemini(self, reply, body):
                self._sse()
                t = reply.get("text") or ""
                for k in range(0, len(t), 6):
                    self._ev(None, {"candidates": [{"content": {"role": "model", "parts": [{"text": t[k:k + 6]}]}}]})
                parts = []
                for name, inp in reply.get("tools", []):
                    parts.append({"functionCall": {"name": name, "args": inp}, "thoughtSignature": "gsig-%d" % len(mock.requests)})
                if parts:
                    self._ev(None, {"candidates": [{"content": {"role": "model", "parts": parts}}]})
                self._ev(None, {"candidates": [{"content": {"role": "model", "parts": [{"text": ""}]}, "finishReason": "STOP"}], "usageMetadata": {"totalTokenCount": 5}})

        self.server = HTTPServer(("127.0.0.1", port), H)
        self.port = self.server.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def all_text_sent(self):
        """Everything AskTim! sent, as one string (for checks that no data went out)."""
        return "\n".join(json.dumps(r["body"], ensure_ascii=False) for r in self.requests)


if __name__ == "__main__":  # a stand-in you can run by hand: python tests/mock_ai.py
    m = MockAI(8765)
    m.default = {"text": "This is AskTim!'s stand-in AI. Here is a command:\n\n```bash\nsamtools flagstat aln.bam\n```\n"}
    print("Stand-in AI at %s - set ASKTIM_ANTHROPIC_URL=%s" % (m.url, m.url))
    while True:
        time.sleep(3600)
