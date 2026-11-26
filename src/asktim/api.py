"""asktim.ask() and asktim.agent() for scripts and notebooks."""

from . import config as _config


def _in_notebook():
    try:
        from IPython import get_ipython

        ip = get_ipython()
        return ip is not None and getattr(ip, "kernel", None) is not None
    except Exception:
        return False


def ask(question, **options):
    if _in_notebook():
        from .notebook import nb_ask

        return nb_ask(question, **options)
    from . import chat
    from .ui import TerminalUI

    cfg = _config.Config()
    return chat.ask(cfg, TerminalUI(cfg), question, files=options.get("files", ()), new=options.get("new", False), model=options.get("model"))


def agent(task, mode="approve", **options):
    if _in_notebook():
        from .notebook import nb_agent

        return nb_agent(task, mode=mode, **options)
    from .agent import Agent
    from .providers import make_provider
    from .ui import TerminalUI

    cfg = _config.Config()
    ui = TerminalUI(cfg)
    ag = Agent(cfg, make_provider(cfg, model=options.get("model")), ui, task, mode=mode, here=options.get("here", False),
               allow_network=options.get("web"), max_steps=options.get("steps"), timeout=options.get("timeout"))
    return ag.run()
