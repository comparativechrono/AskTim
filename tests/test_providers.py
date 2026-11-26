"""The three AI services, against the stand-in: streaming, tool calls, replay, errors, retries."""

import json

import pytest

from asktim import config as C
from asktim.providers import Conversation, ProviderError, ToolResult, make_provider
from asktim.prompts import tool_specs


@pytest.fixture
def cfg(env, monkeypatch):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return lambda provider: C.Config({"provider": provider})


@pytest.mark.parametrize("provider", ["claude", "gemini", "openai"])
def test_stream_text_and_tool_round_trip(cfg, mock, provider):
    p = make_provider(cfg(provider))
    mock.queue = [{"text": "Hello there, student.", "tools": [("run_command", {"command": "ls", "why": "look"})]}, {"text": "Done."}]
    conv = Conversation()
    conv.user("hi")
    got = []
    t = p.complete("sys", conv, tools=tool_specs(), purpose="agent", on_text=got.append)
    assert "".join(got) == "Hello there, student." == t.text
    assert t.tool_calls and t.tool_calls[0].name == "run_command" and t.tool_calls[0].input == {"command": "ls", "why": "look"}
    assert t.stop_reason == "tool_use"
    conv.assistant(t)
    conv.results([ToolResult(t.tool_calls[0].id, "run_command", "exit status 0")])
    t2 = p.complete("sys", conv, tools=tool_specs(), purpose="agent")
    assert t2.text == "Done." and not t2.tool_calls
    body = mock.requests[-1]["body"]
    if provider == "claude":
        blocks = body["messages"][1]["content"]
        assert blocks[0]["type"] == "thinking" and blocks[0]["signature"].startswith("sig-"), "thinking blocks must go back unchanged"
        assert body["messages"][2]["content"][0]["type"] == "tool_result"
        assert body["output_config"] == {"effort": "medium"} and body["cache_control"] == {"type": "ephemeral"}
        headers = {k.lower(): v for k, v in mock.headers[-1].items()}
        assert headers["anthropic-version"] == "2026-10-21" and headers["x-api-key"].startswith("sk-ant-test")
    elif provider == "gemini":
        model_parts = [c for c in body["contents"] if c["role"] == "model"][0]["parts"]
        assert any(p_.get("thoughtSignature") for p_ in model_parts), "thought signatures must go back"
        assert any("functionResponse" in x for c in body["contents"] for x in c["parts"])
        assert body["tools"][0]["functionDeclarations"][0]["parameters"]["type"] == "OBJECT"
    else:
        roles = [m["role"] for m in body["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]
        assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "run_command"


def test_claude_retries_when_overloaded(cfg, mock):
    p = make_provider(cfg("claude"))
    mock.fail_next = [529]
    mock.queue = [{"text": "ok"}]
    notes = []
    conv = Conversation()
    conv.user("hi")
    t = p.complete("sys", conv, on_status=notes.append)
    assert t.text == "ok" and notes and "busy" in notes[0]


@pytest.mark.parametrize("status,kind", [(401, "auth"), (404, "notfound"), (429, "rate")])
def test_claude_errors_are_explained(cfg, mock, status, kind):
    p = make_provider(cfg("claude"))
    mock.fail_next = [status] * 5
    conv = Conversation()
    conv.user("hi")
    with pytest.raises(ProviderError) as e:
        p.complete("sys", conv)
    assert e.value.kind == kind
    assert "AskTim!" in str(e.value) or "wait" in str(e.value).lower()


def test_claude_drops_optional_parameter_it_refuses(cfg, mock):
    p = make_provider(cfg("claude"))
    real = mock.fail_next
    mock.queue = [{"text": "fine"}]

    calls = {"n": 0}
    orig = mock.server.RequestHandlerClass.do_POST

    def first_400(handler):
        calls["n"] += 1
        if calls["n"] == 1:
            n = int(handler.headers.get("content-length") or 0)
            handler.rfile.read(n)
            msg = json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": "output_config: Extra inputs are not permitted"}}).encode()
            handler.send_response(400)
            handler.send_header("content-type", "application/json")
            handler.end_headers()
            handler.wfile.write(msg)
            return
        return orig(handler)

    mock.server.RequestHandlerClass.do_POST = first_400
    try:
        conv = Conversation()
        conv.user("hi")
        assert p.complete("sys", conv).text == "fine"
        assert "output_config" not in mock.requests[-1]["body"]
    finally:
        mock.server.RequestHandlerClass.do_POST = orig


def test_gemini_falls_back_to_the_next_model(cfg, mock):
    p = make_provider(cfg("gemini"))
    mock.fail_next = [503]
    mock.queue = [{"text": "from the fallback"}]
    conv = Conversation()
    conv.user("hi")
    notes = []
    t = p.complete("sys", conv, on_status=notes.append)
    assert t.text == "from the fallback"
    assert "gemini-3.7-flash" in mock.requests[-1]["path"] and "busy" in t.model


def test_no_key_gives_set_up_instructions(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path))
    for v in ("ANTHROPIC_API_KEY", "ASKTIM_CLAUDE_KEY"):
        monkeypatch.delenv(v, raising=False)
    with pytest.raises(ProviderError) as e:
        make_provider(C.Config({"provider": "claude"}))
    assert e.value.kind == "config" and "AskTim! Claude" in str(e.value)


def test_keys_are_stored_privately(tmp_path, monkeypatch):
    import os
    import stat

    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path))
    path = C.save_key("claude", "sk-ant-abc")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert C.find_key("claude")[0] == "sk-ant-abc"
    assert C.forget_key("claude") == ["claude"] and not os.path.exists(path)


def test_site_settings_can_lock_privacy(tmp_path, monkeypatch):
    site = tmp_path / "site.json"
    site.write_text(json.dumps({"privacy": "strict", "course": "Part II practical", "locked": ["privacy"]}))
    monkeypatch.setenv("ASKTIM_SITE_CONFIG", str(site))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("ASKTIM_PRIVACY", "standard")
    c = C.Config({"privacy": "standard"})
    assert c.strict and c.get("course") == "Part II practical"


def test_locked_limits_cannot_be_raised(tmp_path, monkeypatch):
    from asktim.agent import Agent

    site = tmp_path / "site.json"
    site.write_text(json.dumps({"provider": "claude", "agent": {"max_steps": 5, "command_timeout": 60, "allow_network": False},
                                "history_lines": 3, "locked": ["provider", "agent.max_steps", "agent.command_timeout", "agent.allow_network", "history_lines"]}))
    monkeypatch.setenv("ASKTIM_SITE_CONFIG", str(site))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "h"))
    c = C.Config({"provider": "gemini"})
    assert c.provider == "claude"
    ag = Agent(c, provider=None, ui=None, task="t", max_steps=500, timeout=9999, allow_network=True, student_dir=str(tmp_path))
    assert (ag.max_steps, ag.timeout, ag.allow_network) == (5, 60, False)
    ag = Agent(c, provider=None, ui=None, task="t", max_steps=2, timeout=10, student_dir=str(tmp_path))
    assert (ag.max_steps, ag.timeout) == (2, 10)


def test_a_required_sandbox_stops_the_agent_when_missing(tmp_path, monkeypatch):
    from asktim import runner
    from asktim.agent import Agent

    site = tmp_path / "site.json"
    site.write_text(json.dumps({"agent": {"sandbox": "on"}, "locked": ["agent.sandbox"]}))
    monkeypatch.setenv("ASKTIM_SITE_CONFIG", str(site))
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path / "h"))
    monkeypatch.setattr(runner, "make_sandbox", lambda *a, **k: (None, "bubblewrap (bwrap) is not installed"))
    errors = []

    class UI(object):
        def error(self, text):
            errors.append(text)

    student = tmp_path / "student"
    student.mkdir()
    for here in (False, True):
        rec = Agent(C.Config(), provider=None, ui=UI(), task="t", mode="auto", here=here, student_dir=str(student)).run()
        assert rec["outcome"] == "no sandbox"
    assert "bwrap" in errors[0] and "--here" in errors[1]
    assert not (student / "asktim-runs").exists()
