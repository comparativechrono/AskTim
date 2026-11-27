"""A shortcut that starts AskTim!'s window: in the applications menu on Linux, in ~/Applications
on macOS, and in the Start menu on Windows. It runs this Python, so it keeps working while
AskTim! is installed in it."""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _desktop_entry():
    exe = sys.executable
    icon = os.path.join(HERE, "head@2x.png")
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=AskTim!\n"
        "Comment=An AI helper for bioinformatics that keeps your data out of the AI\n"
        "Exec=\"%s\" -m asktim.gui --open\n"
        "Icon=%s\n"
        "Terminal=false\n"
        "Categories=Education;Science;Development;\n"
        "StartupWMClass=AskTim\n" % (exe.replace("\\", "\\\\").replace('"', '\\"'), icon)
    )


def install():
    """Add the shortcut; returns where it is. Raises OSError (or CalledProcessError) when the system refuses."""
    if sys.platform == "darwin":
        app = os.path.expanduser("~/Applications/AskTim!.app")
        os.makedirs(os.path.dirname(app), exist_ok=True)
        cmd = '"%s" -m asktim.gui --open > /dev/null 2>&1 &' % sys.executable.replace('"', '\\"')
        script = 'do shell script "%s"' % cmd.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osacompile", "-o", app, "-e", script], check=True, capture_output=True, timeout=60)
        return app
    if os.name == "nt":
        start = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Microsoft", "Windows", "Start Menu", "Programs")
        os.makedirs(start, exist_ok=True)
        lnk = os.path.join(start, "AskTim!.lnk")
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        target = pyw if os.path.exists(pyw) else exe
        ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); $s.TargetPath = '{t}'; "
              "$s.Arguments = '-m asktim.gui --open'; $s.WorkingDirectory = '{home}'; $s.Save()").format(
                  lnk=lnk.replace("'", "''"), t=target.replace("'", "''"), home=os.path.expanduser("~").replace("'", "''"))
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, capture_output=True, timeout=60)
        return lnk
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    folder = os.path.join(data, "applications")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "asktim.desktop")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_desktop_entry())
    os.chmod(path, 0o755)
    return path
