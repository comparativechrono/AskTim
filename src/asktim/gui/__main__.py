"""python -m asktim.gui [--folder PATH] [--open]: the window, in this process (AskTim! gui starts it like this, in the background)."""

import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    folder = None
    if "--folder" in argv:
        i = argv.index("--folder")
        folder = argv[i + 1] if i + 1 < len(argv) else None
    from . import available

    ok, why = available()
    if not ok:
        sys.stderr.write("AskTim!: %s\n" % why)
        return 2
    from .app import main as run

    return run(folder=folder, open_chat="--open" in argv)


if __name__ == "__main__":
    sys.exit(main())
