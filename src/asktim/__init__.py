"""AskTim! - an AI helper for bioinformatics students.

At the command line::

    AskTim! Claude              # once: store an API key (asked for, not typed on the command line)
    AskTim! "how do I sort a BAM file?"
    AskTim! Agent "map the reads in data/ to ref.fa and count the mapped reads"

In a Jupyter or Colab notebook::

    import asktim               # then, in any cell:
    AskTim! "why did my last cell fail?"
    AskTim! Agent "load counts.csv and plot the library sizes"

AskTim! keeps your data out of the AI: it sends your question, the names of the programs
that are installed, your recent commands (with secrets removed) and descriptions of files
(format, size, columns) - not the data in them. `AskTim! privacy` says exactly what is sent.
"""

from ._version import __version__

__all__ = ["ask", "agent", "load_ipython_extension", "unload_ipython_extension", "__version__"]


def ask(question, **options):
    """Ask AskTim! a question (chat mode: it explains and suggests, and runs nothing).

    In a notebook the answer is shown under the cell; elsewhere it is printed.
    Returns the answer text.
    """
    from .api import ask as _ask

    return _ask(question, **options)


def agent(task, mode="approve", **options):
    """Give AskTim! Agent a task. mode: "approve" (default), "plan", "auto" or "script"."""
    from .api import agent as _agent

    return _agent(task, mode=mode, **options)


def load_ipython_extension(ipython):
    """``%load_ext asktim`` - lets you type ``AskTim! ...`` in notebook cells."""
    from .notebook import load

    load(ipython)


def unload_ipython_extension(ipython):
    from .notebook import unload

    unload(ipython)


def _autoload():
    # `import asktim` in a notebook is enough: register the extension when IPython is running.
    try:
        import sys

        if "IPython" not in sys.modules:
            return
        from IPython import get_ipython

        ip = get_ipython()
        if ip is not None:
            load_ipython_extension(ip)
    except Exception:  # never break an import because of the notebook integration
        pass


_autoload()
