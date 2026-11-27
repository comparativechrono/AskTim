"""Settings and API keys.

Settings come in layers; a later layer wins:

    1. the defaults below
    2. a site file an instructor can provide (/etc/asktim/config.json, or ASKTIM_SITE_CONFIG)
    3. the user's file (~/.config/asktim/config.json)
    4. environment variables (ASKTIM_PROVIDER, ASKTIM_MODEL, ASKTIM_PRIVACY ...)
    5. options given on the command line

A site file may list settings under "locked": those cannot be changed by layers 3-5
(for example a course that wants "privacy": "strict" on its lab machines).

Keys are kept apart from the settings, in ~/.config/asktim/keys.json, which only the owner
can read. Environment variables (ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY) and,
in Google Colab, Colab's Secrets are used when no key has been stored.
"""

import copy
import json
import os
import sys

from . import paths

PROVIDERS = ("claude", "gemini", "openai")
PRIVACY_LEVELS = ("standard", "strict", "off")

# what may be typed for each service
PROVIDER_ALIASES = {
    "claude": "claude",
    "anthropic": "claude",
    "gemini": "gemini",
    "google": "gemini",
    "openai": "openai",
    "chatgpt": "openai",
    "gpt": "openai",
    "openai-compatible": "openai",
    "local": "openai",
}

PROVIDER_NAMES = {"claude": "Anthropic (Claude)", "gemini": "Google Gemini", "openai": "OpenAI-compatible service"}

KEY_ENV = {
    "claude": ("ASKTIM_CLAUDE_KEY", "ANTHROPIC_API_KEY"),
    "gemini": ("ASKTIM_GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai": ("ASKTIM_OPENAI_KEY", "OPENAI_API_KEY"),
}

DEFAULTS = {
    "provider": "claude",
    "models": {
        "claude": "claude-sonnet-5-5",
        "gemini": "gemini-3.8-flash",
        "openai": "gpt-5-mini",
    },
    # When a Gemini model is busy, over its limit or retired, these are asked in turn
    # (the list the practicals used on 6 October 2026).
    "gemini_fallback_models": ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
    "openai_base_url": "https://api.openai.com/v1",
    "anthropic_base_url": "https://api.anthropic.com",
    "gemini_base_url": "https://generativelanguage.googleapis.com",
    "anthropic_version": "2026-10-21",
    # Claude's effort level: lower is faster and cheaper. null leaves the model's default.
    "effort": {"chat": "low", "agent": "medium"},
    "max_tokens": {"chat": 16000, "agent": 32000},
    # what goes with a question
    "send_history": True,
    "history_lines": 20,
    "send_file_names": True,
    # "standard": data are withheld; names of files and columns, help text, errors and summaries are sent.
    # "strict": also no column names or file names, and of a command's output only errors and help text.
    # "off": the data filter is off (AskTim! privacy off): data can be sent; secrets and personal details are still removed.
    "privacy": "standard",
    # what AskTim! prints besides the AI's answer: false - only what is needed; true - everything (-v)
    "verbose": False,
    "agent": {
        "max_steps": 25,
        "command_timeout": 600,
        "output_chars": 4000,
        "allow_network": False,
        "sandbox": "auto",  # auto | on | off  (bubblewrap on Linux, when it works)
        "runs_dir": "asktim-runs",
    },
    # extra lines an instructor can add to what the AI is told (the course, the data, house rules)
    "course": "",
    "color": "auto",  # auto | always | never
    "chat_memory_minutes": 60,
    "log": True,
    "locked": [],
}

ENV_SETTINGS = {
    "ASKTIM_PROVIDER": "provider",
    "ASKTIM_PRIVACY": "privacy",
    "ASKTIM_COURSE": "course",
    "ASKTIM_COLOR": "color",
    "ASKTIM_VERBOSE": "verbose",
    "ASKTIM_OPENAI_URL": "openai_base_url",
    "ASKTIM_ANTHROPIC_URL": "anthropic_base_url",
    "ASKTIM_GEMINI_URL": "gemini_base_url",
}


def _deep_update(base, extra):
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        sys.stderr.write("AskTim!: could not read %s (%s) - it is ignored.\n" % (path, e))
        return {}


def config_file():
    return os.path.join(paths.config_dir(), "config.json")


def keys_file():
    return os.path.join(paths.config_dir(), "keys.json")


def get_path(d, dotted):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(d, dotted, value):
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


class Config(object):
    """The settings in force, with the layer each came from."""

    def __init__(self, overrides=None):
        self.site = _read_json(paths.site_config_file())
        self.user = _read_json(config_file())
        data = copy.deepcopy(DEFAULTS)
        _deep_update(data, {k: v for k, v in self.site.items() if k != "locked"})
        locked = list(self.site.get("locked") or [])
        site_values = {k: get_path(data, k) for k in locked}
        _deep_update(data, self.user)
        for env, key in ENV_SETTINGS.items():
            if os.environ.get(env):
                _set_path(data, key, os.environ[env])
        if os.environ.get("ASKTIM_MODEL"):
            prov = normalise_provider(data.get("provider")) or "claude"
            data["models"][prov] = os.environ["ASKTIM_MODEL"]
        for k, v in (overrides or {}).items():
            if v is not None:
                _set_path(data, k, v)
        # the instructor's locked values win over everything the user sets
        for k, v in site_values.items():
            if v is not None:
                _set_path(data, k, v)
        data["locked"] = locked
        data["provider"] = normalise_provider(data.get("provider")) or "claude"
        if data.get("privacy") not in PRIVACY_LEVELS:
            data["privacy"] = "standard"
        self.data = data

    def __getitem__(self, key):
        return get_path(self.data, key)

    def get(self, key, default=None):
        v = get_path(self.data, key)
        return default if v is None else v

    @property
    def provider(self):
        return self.data["provider"]

    def model(self, provider=None):
        return self.data["models"].get(provider or self.provider) or DEFAULTS["models"].get(provider or self.provider)

    @property
    def strict(self):
        return self.data.get("privacy") == "strict"

    @property
    def share_data(self):
        """The user has turned the data filter off (AskTim! privacy off)."""
        return self.data.get("privacy") == "off"

    @property
    def verbose(self):
        v = self.data.get("verbose")
        return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")

    def is_locked(self, key):
        return any(key == k or key.startswith(k + ".") for k in self.data.get("locked", []))


def normalise_provider(name):
    if not name:
        return None
    return PROVIDER_ALIASES.get(str(name).strip().lower())


def save_user_setting(key, value):
    """Change one setting in the user's config.json (dotted keys: "agent.max_steps")."""
    data = _read_json(config_file())
    _set_path(data, key, value)
    paths.ensure(paths.config_dir(), private=True)
    tmp = config_file() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, config_file())


# ---------------------------------------------------------------------------------------------
# keys
# ---------------------------------------------------------------------------------------------


def _write_private(path, text):
    paths.ensure(os.path.dirname(path), private=True)
    tmp = path + ".tmp"
    # create the file with owner-only permissions from the start, so a key is never readable by others
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    if not sys.platform.startswith("win"):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def stored_keys():
    return {k: v for k, v in _read_json(keys_file()).items() if isinstance(v, str) and v}


def save_key(provider, key):
    keys = stored_keys()
    keys[provider] = key.strip()
    _write_private(keys_file(), json.dumps(keys, indent=2))
    return keys_file()


def forget_key(provider=None):
    """Delete a stored key (or all of them). Returns the providers removed."""
    keys = stored_keys()
    gone = [p for p in keys if provider in (None, p)]
    for p in gone:
        keys.pop(p, None)
    if keys:
        _write_private(keys_file(), json.dumps(keys, indent=2))
    else:
        try:
            os.remove(keys_file())
        except FileNotFoundError:
            pass
    return gone


def in_colab():
    return "google.colab" in sys.modules


def _colab_secret(provider):
    """A key from Colab's Secrets (the key icon in Colab's left bar). Colab asks the user first."""
    if not in_colab():
        return None
    try:
        from google.colab import userdata  # type: ignore
    except Exception:
        return None
    names = list(KEY_ENV[provider]) + ["ASKTIM_KEY"]
    for name in names:
        try:
            v = userdata.get(name)
        except Exception:
            v = None
        if v:
            return v.strip()
    return None


def find_key(provider):
    """(key, where it came from) or (None, None)."""
    keys = stored_keys()
    if keys.get(provider):
        return keys[provider], "saved in " + paths.pretty(keys_file())
    for env in KEY_ENV[provider]:
        if os.environ.get(env):
            return os.environ[env].strip(), "the environment variable " + env
    v = _colab_secret(provider)
    if v:
        return v, "Colab's Secrets"
    return None, None


def mask(key):
    if not key:
        return ""
    if len(key) <= 12:
        return key[:2] + "…"
    return key[:7] + "…" + key[-4:]


def looks_like_key(token):
    """Is this command-line word an API key (rather than the start of a question)?"""
    t = (token or "").strip()
    if len(t) < 20 or any(c.isspace() for c in t):
        return False
    if t.startswith(("sk-", "AIza", "AQ.", "gsk_", "hf_", "xai-", "pplx-")):
        return True
    letters = sum(c.isalpha() for c in t)
    digits = sum(c.isdigit() for c in t)
    return letters > 4 and digits > 2 and all(c.isalnum() or c in "-_.:" for c in t)
