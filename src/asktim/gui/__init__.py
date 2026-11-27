"""AskTim!'s window: a floating head on the desktop that opens a chat (with the agent) and the settings.

`AskTim! gui` starts it in the background, so the terminal stays free. It needs Tk, which comes with
Python on Windows and macOS (python.org); on Linux it is a separate package (python3-tk).
"""

import os
import sys


def install_hint():
    """How to get Tk for this Python."""
    exe = sys.executable
    if os.path.exists(os.path.join(sys.prefix, "conda-meta")):
        return "Install it in this conda environment:  conda install tk"
    if sys.platform == "darwin":
        if "homebrew" in exe.lower() or "/opt/homebrew" in exe or "/usr/local/Cellar" in os.path.realpath(exe):
            return "Install it with Homebrew:  brew install python-tk@%d.%d" % sys.version_info[:2]
        return "The Python from python.org has it; so does Homebrew's, after:  brew install python-tk@%d.%d" % sys.version_info[:2]
    if os.name == "nt":
        return "Run the Python installer again, choose Modify, and tick 'tcl/tk and IDLE'."
    ids = ""
    try:
        with open("/etc/os-release") as f:
            for ln in f:
                if ln.startswith(("ID=", "ID_LIKE=")):
                    ids += " " + ln.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    if any(w in ids for w in ("debian", "ubuntu")):
        return "Install it with:  sudo apt install python3-tk   (or ask whoever looks after this computer)"
    if any(w in ids for w in ("fedora", "rhel", "centos")):
        return "Install it with:  sudo dnf install python3-tkinter   (or ask whoever looks after this computer)"
    if "arch" in ids:
        return "Install it with:  sudo pacman -S tk"
    if "suse" in ids:
        return "Install it with:  sudo zypper install python3-tk"
    return "Install Tk for this Python (%s): the package is usually called python3-tk or python3-tkinter." % exe


def available():
    """(True, None) when the window can be shown here, else (False, why and what to do)."""
    try:
        import tkinter
    except ImportError:
        return False, "This Python has no Tk, which AskTim!'s window needs. " + install_hint()
    del tkinter
    if sys.platform != "darwin" and os.name != "nt" and not os.environ.get("DISPLAY"):
        if os.environ.get("WAYLAND_DISPLAY"):
            return False, "AskTim!'s window needs X11 (Tk does not speak Wayland), and DISPLAY is not set. Turn on XWayland, or use AskTim! in the terminal."
        return False, ("There is no screen here (DISPLAY is not set): AskTim!'s window needs a desktop. In a terminal on a server, "
                       "or in Colab, ask at the command line instead:  AskTim! \"your question\"")
    return True, None
