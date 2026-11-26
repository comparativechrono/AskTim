"""`AskTim! setup-shell`: a few lines in ~/.bashrc, ~/.zshrc or fish's config.

They do two things, and nothing else:
- after each command, append "time, exit status, folder, command" to
  ~/.local/state/asktim/commands.tsv, so that AskTim! sees your latest commands and knows
  which one failed (the output of commands is never recorded);
- Alt+A puts the next command from AskTim!'s last answer on your command line, for you to
  read, change and run yourself (bash and zsh);
- in zsh, ? and * in a question are not read as wildcards, so questions need no quotes.

`AskTim! setup-shell --remove` takes the lines out again.
"""

import os
import re

from . import paths

BEGIN = "# >>> AskTim! shell integration >>>"
END = "# <<< AskTim! shell integration <<<"

BASH = r'''# >>> AskTim! shell integration >>>
# AskTim! sees the commands you type (never their output) and which ones failed.
# A command that starts with a space is not recorded.
# Alt+A puts the next command from AskTim!'s last answer on your command line.
# To turn this off: AskTim! setup-shell --remove   (or delete these lines)
if [ -n "$BASH_VERSION" ] && [ -z "$__ASKTIM_HOOKED" ]; then
  __ASKTIM_HOOKED=1
  __asktim_log="${ASKTIM_HOME:+$ASKTIM_HOME/state}"; __asktim_log="${__asktim_log:-${XDG_STATE_HOME:-$HOME/.local/state}/asktim}/commands.tsv"
  __asktim_last_n=""
  __asktim_prompt() {
    local s=$? h n c
    h=$(HISTTIMEFORMAT= builtin history 1 2>/dev/null)
    h="${h#"${h%%[![:space:]]*}"}"; n="${h%%[[:space:]]*}"; c="${h#"$n"}"; c="${c#"${c%%[![:space:]]*}"}"
    if [ -n "$c" ] && [ "$n" != "$__asktim_last_n" ]; then
      __asktim_last_n=$n
      c=${c//$'\t'/ }; c=${c//$'\n'/$'\x1e'}
      if [ "${BASH_VERSINFO[0]}" -ge 4 ] 2>/dev/null; then
        printf '%(%s)T\t%s\t%s\t%s\n' -1 "$s" "$PWD" "$c" >> "$__asktim_log" 2>/dev/null
      else
        printf '%s\t%s\t%s\t%s\n' "$(date +%s)" "$s" "$PWD" "$c" >> "$__asktim_log" 2>/dev/null
      fi
    fi
    return $s
  }
  case ";${PROMPT_COMMAND:-};" in *";__asktim_prompt;"*) ;; *) PROMPT_COMMAND="__asktim_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}";; esac
  __asktim_insert() {
    local f="${ASKTIM_HOME:+$ASKTIM_HOME/cache}"; f="${f:-${XDG_CACHE_HOME:-$HOME/.cache}/asktim}/suggested.txt"
    [ -s "$f" ] || return 0
    local i=0 line idxf="$f.next"; [ -s "$idxf" ] && i=$(cat "$idxf")
    line=$(sed -n "$((i + 1))p" "$f")
    [ -z "$line" ] && { i=0; line=$(sed -n '1p' "$f"); }
    echo $((i + 1)) > "$idxf"
    READLINE_LINE="$line"; READLINE_POINT=${#line}
  }
  [[ $- == *i* ]] && [ "${BASH_VERSINFO[0]}" -ge 4 ] 2>/dev/null && bind -x '"\ea": __asktim_insert' 2>/dev/null
fi
# <<< AskTim! shell integration <<<
'''

ZSH = r'''# >>> AskTim! shell integration >>>
# AskTim! sees the commands you type (never their output) and which ones failed.
# A command that starts with a space is not recorded.
# Alt+A puts the next command from AskTim!'s last answer on your command line.
# To turn this off: AskTim! setup-shell --remove   (or delete these lines)
if [ -n "$ZSH_VERSION" ] && [ -z "$__ASKTIM_HOOKED" ]; then
  typeset -g __ASKTIM_HOOKED=1
  typeset -g __asktim_log="${ASKTIM_HOME:+$ASKTIM_HOME/state}"; __asktim_log="${__asktim_log:-${XDG_STATE_HOME:-$HOME/.local/state}/asktim}/commands.tsv"
  typeset -g __asktim_cmd=""
  zmodload zsh/datetime 2>/dev/null
  __asktim_preexec() { [[ "$1" == " "* ]] && __asktim_cmd="" || __asktim_cmd="$1" }
  __asktim_precmd() {
    local s=$?
    if [[ -n "$__asktim_cmd" ]]; then
      local c="${__asktim_cmd//$'\t'/ }"; c="${c//$'\n'/$'\x1e'}"
      print -r -- "${EPOCHSECONDS:-0}"$'\t'"$s"$'\t'"$PWD"$'\t'"$c" >> "$__asktim_log" 2>/dev/null
      __asktim_cmd=""
    fi
    return $s
  }
  autoload -Uz add-zsh-hook
  add-zsh-hook preexec __asktim_preexec
  add-zsh-hook precmd __asktim_precmd
  __asktim_insert() {
    local f="${ASKTIM_HOME:+$ASKTIM_HOME/cache}"; f="${f:-${XDG_CACHE_HOME:-$HOME/.cache}/asktim}/suggested.txt"
    [[ -s "$f" ]] || return 0
    local i=0 line idxf="$f.next"; [[ -s "$idxf" ]] && i=$(<"$idxf")
    line=$(sed -n "$((i + 1))p" "$f")
    [[ -z "$line" ]] && { i=0; line=$(sed -n '1p' "$f"); }
    print -r -- $((i + 1)) > "$idxf"
    BUFFER="$line"; CURSOR=${#BUFFER}
  }
  zle -N __asktim_insert
  bindkey '\ea' __asktim_insert
  # questions need no quotes: zsh does not read ? and * in them as wildcards
  alias 'AskTim!'='noglob AskTim!'
  alias asktim='noglob asktim'
fi
# <<< AskTim! shell integration <<<
'''

FISH = r'''# >>> AskTim! shell integration >>>
# AskTim! sees the commands you type (never their output) and which ones failed.
# To turn this off: AskTim! setup-shell --remove   (or delete this file)
set -l __asktim_state (set -q ASKTIM_HOME; and echo $ASKTIM_HOME/state; or begin; set -q XDG_STATE_HOME; and echo $XDG_STATE_HOME/asktim; or echo $HOME/.local/state/asktim; end)
set -g __asktim_log $__asktim_state/commands.tsv
function __asktim_postexec --on-event fish_postexec
    set -l s $status
    string match -q -r '^ ' -- $argv[1]; and return
    set -l c (string join \x1e -- $argv[1] | string replace -a \t ' ')
    printf '%s\t%s\t%s\t%s\n' (date +%s) $s $PWD "$c" >> $__asktim_log 2>/dev/null
end
# <<< AskTim! shell integration <<<
'''


def rc_file(shell):
    home = os.path.expanduser("~")
    if shell == "zsh":
        return os.path.join(os.environ.get("ZDOTDIR", home), ".zshrc")
    if shell == "fish":
        cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        return os.path.join(cfg, "fish", "conf.d", "asktim.fish")
    return os.path.join(home, ".bashrc")


def snippet(shell):
    return {"zsh": ZSH, "fish": FISH}.get(shell, BASH)


def _strip_block(text):
    return re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)


def install(shell):
    """Add (or update) the lines. Returns (file, already_there)."""
    paths.ensure(paths.state_dir(), private=True)
    paths.ensure(paths.cache_dir(), private=True)
    f = rc_file(shell)
    try:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    had = BEGIN in text
    text = _strip_block(text)
    if text and not text.endswith("\n"):
        text += "\n"
    text += ("\n" if text and not had else "") + snippet(shell)
    os.makedirs(os.path.dirname(f) or ".", exist_ok=True)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(text)
    # start the log, so that `AskTim! status` can tell the hook is there
    log = paths.commands_log()
    if not os.path.exists(log):
        os.close(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))  # readable by you only, like your shell history
    return f, had


def remove(shell):
    f = rc_file(shell)
    try:
        with open(f, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return f, False
    if BEGIN not in text:
        return f, False
    new = _strip_block(text)
    if shell == "fish" and not new.strip():
        os.remove(f)
    else:
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(new)
    return f, True


def installed(shell):
    try:
        with open(rc_file(shell), encoding="utf-8") as fh:
            return BEGIN in fh.read()
    except OSError:
        return False
