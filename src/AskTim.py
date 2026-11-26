"""`import AskTim` is the same as `import asktim` (the package on PyPI is called AskTim).

In a notebook, either one makes the AskTim! command work in cells: AskTim! "your question".
"""

from asktim import *  # noqa: F401,F403
from asktim import __version__, agent, ask, load_ipython_extension, unload_ipython_extension  # noqa: F401
