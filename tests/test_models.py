"""Choosing a model: short names (opus, sonnet, haiku; flash, pro) and the service's own list."""

from conftest import run_asktim

from asktim import config as _config
from asktim import models


def _out(r):
    return r.stdout + r.stderr


def test_short_names_and_the_list_of_models(env, mock, project):
    r = run_asktim(["models"], env, project)
    assert r.returncode == 0, _out(r)
    assert "claude-opus-5-5" in r.stdout and "→ claude-sonnet-5-5" in r.stdout and "opus: more capable than Sonnet" in r.stdout
    gets = [q for q in mock.requests if q.get("method") == "GET"]
    assert gets and gets[-1]["path"].startswith("/v1/models")
    r = run_asktim(["model", "opus"], env, project)
    assert "AskTim! will use claude-opus-5-5 for Anthropic (Claude) (opus: the newest opus model that the service lists)" in r.stdout, _out(r)
    assert run_asktim(["model"], env, project).stdout.startswith("Anthropic (Claude): claude-opus-5-5")
    run_asktim(["-q", "hello"], env, project)
    assert mock.requests[-1]["body"]["model"] == "claude-opus-5-5"
    # a full name is kept as it is
    r = run_asktim(["model", "claude-haiku-5-5"], env, project)
    assert "AskTim! will use claude-haiku-5-5 for Anthropic (Claude)." in r.stdout
    # Gemini: "flash" is the newest flash model by its plain name - not a preview, not flash-lite
    assert run_asktim(["use", "gemini"], env, project).returncode == 0
    r = run_asktim(["model", "flash"], env, project)
    assert "AskTim! will use gemini-3.8-flash for Google Gemini" in r.stdout, _out(r)
    r = run_asktim(["models"], env, project)
    assert "gemini-3.8-pro" in r.stdout and "text-embedding" not in r.stdout  # (a model that cannot answer is left out)
    r = run_asktim(["model", "sonnet"], env, project)
    assert r.returncode == 2 and "AskTim! use claude" in _out(r)


def test_short_names_without_the_service(env, project):
    e = dict(env, ASKTIM_ANTHROPIC_URL="http://127.0.0.1:9")  # (nothing answers there)
    r = run_asktim(["model", "opus"], e, project)
    assert "claude-opus-5-5" in r.stdout and "AskTim!'s list of current models" in r.stdout, _out(r)
    r = run_asktim(["models"], e, project)
    assert r.returncode == 1 and "opus (claude-opus-5-5)" in _out(r)


def test_what_the_list_says_of_each_model():
    found = [models.Model(i) for i in ("claude-fable-5-2", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5",
                                       "claude-opus-4-1", "claude-sonnet-4-5")]
    n = models.notes(found, "claude")
    # each family's newest model says what the family is for; the older ones say nothing
    assert n["claude-opus-5-5"] == "opus: more capable than Sonnet; slower and dearer"
    assert n["claude-haiku-5-5"] == "haiku: the fastest and cheapest"
    assert n["claude-sonnet-5-5"].startswith("sonnet: fast and capable")
    assert "claude-opus-4-1" not in n and "claude-sonnet-4-5" not in n
    assert "claude-fable-5-2" not in n  # (no short name: nothing is claimed for it)
    # and AskTim!'s own default says so
    default = _config.DEFAULTS["models"]["claude"]
    assert n[default].endswith("AskTim!'s default")
    g = models.notes([models.Model(i) for i in ("gemini-3.8-pro", "gemini-3.8-flash", "gemini-3.8-flash-lite")], "gemini")
    assert g["gemini-3.8-pro"] == "pro: Gemini's most capable"
    assert g["gemini-3.8-flash-lite"].startswith("flash-lite: the fastest and cheapest")
    assert models.notes([models.Model("llama3")], "openai") == {}


def test_resolving_names(tmp_path, monkeypatch):
    monkeypatch.setenv("ASKTIM_HOME", str(tmp_path))
    monkeypatch.setenv("ASKTIM_SITE_CONFIG", str(tmp_path / "none.json"))
    cfg = _config.Config()
    listed = [models.Model(i) for i in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-4-1")]
    assert models.resolve(cfg, "opus", "claude", available=listed)[0] == "claude-opus-5-5"
    assert models.resolve(cfg, "Opus", "claude", available=False)[0] == "claude-opus-5-5"
    assert models.resolve(cfg, "claude-opus-4-1", "claude", available=listed)[0] == "claude-opus-4-1"
    # a local service's own names are never taken for short names
    assert models.resolve(cfg, "mistral", "openai")[0] == "mistral"
    try:
        models.resolve(cfg, "pro", "claude", available=listed)
        raise AssertionError("no Claude model is called pro")
    except ValueError as e:
        assert "pro" in str(e)
    g = [models.Model(i) for i in ("gemini-3.8-flash-preview-11-2026", "gemini-3.8-flash", "gemini-3.8-flash-lite", "gemini-3.5-flash")]
    assert models.resolve(cfg, "flash", "gemini", available=g)[0] == "gemini-3.8-flash"
    assert models.resolve(cfg, "flash-lite", "gemini", available=g)[0] == "gemini-3.8-flash-lite"
