"""Programs with windows that the agent may start to show the user a file (open_file).

Only programs that are installed can be used, and the user is asked first every time. They run
outside the sandbox, as programs the user starts themselves do (they need the screen, and some -
IGV, for example - fetch genomes from the internet); their environment has no API keys or tokens.
"""

import glob
import os
import shutil
import subprocess
import sys

# name the agent uses -> name of the macOS app (in /Applications), where it differs
VIEWERS = {
    # genomes, alignments, trees, networks
    "igv": "IGV", "igv.sh": "IGV", "jalview": "Jalview", "aliview": "AliView", "seaview": "SeaView", "ugene": "Unipro UGENE",
    "tablet": "Tablet", "artemis": "Artemis", "act": "ACT", "figtree": "FigTree", "dendroscope": "Dendroscope",
    "cytoscape": "Cytoscape", "pymol": "PyMOL", "chimerax": "ChimeraX", "jmol": "Jmol",
    # browsers, documents, images, text
    "firefox": "Firefox", "google-chrome": "Google Chrome", "chromium": "Chromium", "chromium-browser": "Chromium",
    "evince": None, "okular": None, "zathura": None, "eog": None, "feh": None, "gimp": "GIMP", "inkscape": "Inkscape",
    "libreoffice": "LibreOffice", "soffice": "LibreOffice", "gedit": None, "kate": None, "mousepad": None, "xed": None,
    "pluma": None, "geany": None, "code": "Visual Studio Code", "rstudio": "RStudio",
}


def can_show_windows():
    """(yes or no, why not)"""
    if sys.platform == "darwin":
        if os.environ.get("SSH_CONNECTION") and not os.environ.get("DISPLAY"):
            return False, "you are logged in over SSH, so there is no screen to open windows on"
        return True, ""
    if os.name == "nt":
        return True, ""
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True, ""
    return False, "this computer has no screen for windows (no DISPLAY)"


def _mac_app(name):
    app = VIEWERS.get(name)
    if sys.platform != "darwin" or not app:
        return None
    for root in ("/Applications", os.path.expanduser("~/Applications")):
        hits = sorted(glob.glob(os.path.join(root, app + "*.app")))
        if hits:
            return hits[0]
    return None


def find(name):
    """(the command to start it with a file, or None; why not)"""
    name = (name or "").strip()
    base = os.path.basename(name)
    if base not in VIEWERS:
        return None, "%s is not a program AskTim! starts (it can start: %s)" % (name, ", ".join(sorted(VIEWERS)))
    exe = shutil.which(base)
    if exe:
        return [exe], ""
    app = _mac_app(base)
    if app:
        return ["open", "-a", app], ""
    return None, "%s is not installed here" % base


def installed():
    return [n for n in sorted(VIEWERS) if shutil.which(n) or _mac_app(n)]


def describe():
    """A line for the agent: which programs it could start, if any."""
    ok, why = can_show_windows()
    if not ok:
        return "open_file cannot start programs here: %s. Tell the user which file to open instead." % why
    progs = installed()
    if not progs:
        return "No programs with windows that AskTim! can start are installed; open_file uses the computer's default program for the file."
    return "Programs with windows installed here (open_file can name one, e.g. IGV for a BAM or VCF file): %s." % ", ".join(progs)


def launch(path, name):
    """Start a program on a file without waiting for it. (ok, how)"""
    ok, why = can_show_windows()
    if not ok:
        return False, why
    argv, why = find(name)
    if argv is None:
        return False, why
    from .runner import clean_env

    try:
        subprocess.Popen(argv + [path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=clean_env())
    except OSError as e:
        return False, str(e)
    return True, "in %s" % os.path.basename(name)
