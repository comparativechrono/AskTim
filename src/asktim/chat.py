"""Chat mode: AskTim! explains and suggests; it runs nothing."""

import os

from . import context, history, inventory, logbook, offline, privacy, prompts
from .providers import Conversation, ProviderError, make_provider


def offline_answer(ui, question, stdin_text=None, reason=None):
    """Built-in help when no AI can be used."""
    t = ui.t
    lines = []
    if stdin_text:
        lines += offline.explain_text(stdin_text)
    cmds, _src = history.recent(5)
    last = history.last_failed(cmds[-2:]) if cmds else None
    if last is not None:
        lines.insert(0, "Your last command, `%s`, failed. %s" % (privacy.clean(last.line)[0], " ".join(offline.explain_command(last))))
    lines += offline.explain_text(question or "")
    if reason:
        t.warn(reason)
    if lines:
        t.line(t.s("AskTim!'s built-in help (no AI):", "bold"))
        for ln in lines:
            t.line("%s %s" % (t.bullet(), ln))
    else:
        t.line("AskTim!'s built-in help knows common error messages: pipe one in, e.g.  my_command 2>&1 | AskTim! \"what does this mean?\"")
    return "\n".join(lines)


def ask(cfg, ui, question, files=(), stdin_text=None, new=False, dry_run=False, provider=None, model=None, send_history=None, history_n=None, send_files=None):
    """Answer one question at the command line. Returns the answer text (or None)."""
    question = (question or "").strip()
    if not question and stdin_text:
        question = "What does this mean, and what should I do?"
    # text pasted into a question: data lines are withheld as from any output (unless the data filter is off)
    q_clean, n_q, pasted = privacy.clean_question(question, strict=cfg.strict, share_data=cfg.share_data)
    inv = inventory.gather()
    system = prompts.chat_system(inventory.describe(inv), cfg, notebook=False)
    g = context.terminal(cfg, files=files, stdin_text=stdin_text, send_history=send_history, history_n=history_n, send_files=send_files)
    g.redactions += n_q
    g.withheld += pasted
    if pasted and ui.verbose:
        ui.t.warn("AskTim! kept %d piece%s of data in your question from the AI (lines or records that look like data); "
                  "it is told only what kind of data was there." % (pasted, "" if pasted == 1 else "s"))
    items = [] if new else logbook.load_chat(int(cfg.get("chat_memory_minutes") or 60))
    conv = Conversation.from_json(items)
    message = g.text() + "Question: " + q_clean
    if dry_run:
        t = ui.t
        t.rule("What AskTim! would send (nothing has been sent)", "cyan")
        t.line(t.s("System prompt (the same for every question):", "bold"))
        t.line(system)
        t.line()
        if items:
            t.line(t.s("The conversation so far: %d earlier messages" % len(items), "bold"))
        t.line(t.s("Your message, with its context:", "bold"))
        t.line(message)
        t.rule("", "cyan")
        t.note(g.summary())
        return None
    conv.user(message)
    try:
        prov = provider or make_provider(cfg, model=model)
    except ProviderError as e:
        if e.kind == "config":
            offline_answer(ui, question, stdin_text)
            ui.t.line()
            ui.t.note(str(e))
            return None
        raise
    ui.data_off_reminder(cfg)
    ui.chat_header(g.summary(), prov.describe())
    ui.thinking()
    try:
        turn = prov.complete(system, conv, purpose="chat", on_text=ui.stream_text, on_status=ui.status)
    except KeyboardInterrupt:
        ui.end_text()
        ui.t.note("(stopped)")
        return None
    except ProviderError as e:
        ui.end_text()
        ui.t.error(str(e))
        if e.kind in ("network", "busy", "rate"):
            offline_answer(ui, question, stdin_text)
        return None
    blocks = ui.md.blocks if ui.md is not None else []
    ui.end_text()
    if not turn.text.strip():
        ui.t.warn("The model sent an empty answer. Ask again – a second try usually works.")
        return None
    if turn.stop_reason == "refusal":
        ui.t.note("(The model declined to answer this.)")
    # keep the conversation (text only, without the context) for follow-up questions
    conv.items[-1] = {"kind": "user", "text": "Question: " + q_clean}
    conv.assistant(turn)
    logbook.save_chat(conv.to_json())
    from .render import code_blocks

    cmds = logbook.save_suggested(code_blocks(turn.text) or blocks)
    if cmds and ui.t.interactive():
        from . import shell_setup

        sh = history.detect_shell()
        if sh in ("bash", "zsh") and shell_setup.installed(sh):
            ui.note("Alt+A puts these commands on your command line, one at a time.")
    logbook.add({"mode": "chat", "question": q_clean, "model": turn.model, "provider": prov.name, "sent": g.sent, "cwd": context.paths.pretty(os.getcwd())}, cfg)
    return turn.text
