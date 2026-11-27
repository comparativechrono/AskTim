"""Which file the AskTim! command is (everything else is in pyproject.toml).

On Linux and macOS it is the script file bin/AskTim!. Windows cannot run a file without an
extension (it asks which app should open it), so the wheels for Windows have bin/AskTim!.cmd
instead: the release builds them with ASKTIM_WINDOWS_WHEEL=1, and a build on Windows itself (from
the source archive) does the same.
"""

import os

from setuptools import setup

windows = os.environ.get("ASKTIM_WINDOWS_WHEEL", "1" if os.name == "nt" else "0") == "1"
setup(scripts=["bin/AskTim!.cmd"] if windows else ["bin/AskTim!"])
