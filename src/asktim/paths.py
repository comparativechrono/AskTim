"""Where AskTim! keeps its files.

    config   ~/.config/asktim        settings (config.json) and keys (keys.json, readable by you only)
    cache    ~/.cache/asktim         the list of installed programs, the current conversation
    state    ~/.local/state/asktim   the log of your questions, the commands the shell hook records

ASKTIM_HOME=/some/folder puts all three under that folder (useful for tests and shared machines).
The XDG_* variables are respected; on Windows %APPDATA% and %LOCALAPPDATA% are used.
"""

import os
import sys


def _home():
    return os.path.expanduser("~")


def _base(kind):
    override = os.environ.get("ASKTIM_HOME")
    if override:
        return os.path.join(os.path.expanduser(override), kind)
    if sys.platform.startswith("win"):
        root = os.environ.get("APPDATA" if kind == "config" else "LOCALAPPDATA") or os.path.join(_home(), "AppData", "Roaming")
        return os.path.join(root, "asktim", kind if kind != "config" else "")
    env = {"config": "XDG_CONFIG_HOME", "cache": "XDG_CACHE_HOME", "state": "XDG_STATE_HOME"}[kind]
    default = {"config": ".config", "cache": ".cache", "state": os.path.join(".local", "state")}[kind]
    root = os.environ.get(env) or os.path.join(_home(), default)
    return os.path.join(root, "asktim")


def config_dir():
    return _base("config")


def cache_dir():
    return _base("cache")


def state_dir():
    return _base("state")


def ensure(path, private=False):
    """Make a folder (and its parents); private=True makes it readable by the owner only."""
    os.makedirs(path, exist_ok=True)
    if private and not sys.platform.startswith("win"):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def site_config_file():
    """A settings file that an instructor can put on a shared machine (read before the user's)."""
    p = os.environ.get("ASKTIM_SITE_CONFIG")
    if p:
        return p
    if sys.platform.startswith("win"):
        return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "asktim", "config.json")
    return "/etc/asktim/config.json"


def commands_log():
    """The file the shell hook appends to: time, exit status, folder, command (one per line)."""
    return os.path.join(state_dir(), "commands.tsv")


def pretty(path):
    """A path as the student would write it: ~ for the home folder."""
    try:
        path = os.path.abspath(path)
        home = _home()
        if path == home:
            return "~"
        if path.startswith(home + os.sep):
            return "~" + path[len(home):]
    except Exception:
        pass
    return path
