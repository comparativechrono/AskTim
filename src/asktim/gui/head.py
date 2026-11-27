"""The floating head: Tim's picture on the desktop, above the other windows. Click it to open the
chat, drag it to move it, right-click (or Control-click) for more.

Only the picture shows, not a square around it: macOS and Windows have transparent windows in Tk;
on Linux (X11) the window is cut to the picture's shape with the X11 SHAPE extension. Where that
is not possible the picture keeps a plain square background.
"""

import os
import sys
import time

import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
KEY = "#ff00fe"  # Windows: the colour that becomes transparent (not used in the picture)
_MASKS = {}


def image_file(big=False, hard=False):
    return os.path.join(HERE, "head%s%s.png" % ("@2x" if big else "", "-hard" if hard else ""))


def _x11_shape(win, image):
    """Cut an X11 window to the opaque pixels of a picture (1 bit of transparency). True if it worked."""
    try:
        import ctypes
        import ctypes.util

        x11 = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        xext = ctypes.CDLL(ctypes.util.find_library("Xext") or "libXext.so.6")
    except OSError:
        return False
    w, h = image.width(), image.height()
    stride = (w + 7) // 8
    bits = _MASKS.get(str(image))
    if bits is None:
        bits = bytearray(stride * h)
        for y in range(h):
            for x in range(w):
                if not image.transparency_get(x, y):
                    bits[y * stride + x // 8] |= 1 << (x % 8)
        _MASKS[str(image)] = bits
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XCreateBitmapFromData.restype = ctypes.c_ulong
    x11.XCreateBitmapFromData.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
    x11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    xext.XShapeCombineMask.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int]
    name = win.winfo_screen().encode() or None
    dpy = x11.XOpenDisplay(name)
    if not dpy:
        return False
    x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                               ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
    x11.XFree.argtypes = [ctypes.c_void_p]
    try:
        # Tk puts each toplevel inside a wrapper window of its own: the window to cut is the one whose parent is the root
        wid = win.winfo_id()
        for _ in range(8):
            root, parent = ctypes.c_ulong(), ctypes.c_ulong()
            children, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
            if not x11.XQueryTree(dpy, wid, ctypes.byref(root), ctypes.byref(parent), ctypes.byref(children), ctypes.byref(n)):
                break
            if children:
                x11.XFree(children)
            if not parent.value or parent.value == root.value:
                break
            wid = parent.value
        pix = x11.XCreateBitmapFromData(dpy, wid, bytes(bits), w, h)
        if not pix:
            return False
        xext.XShapeCombineMask(dpy, wid, 0, 0, 0, pix, 0)  # ShapeBounding, ShapeSet
        x11.XFreePixmap(dpy, pix)
        x11.XFlush(dpy)
        return True
    except Exception:
        return False
    finally:
        x11.XCloseDisplay(dpy)


class Head(object):
    def __init__(self, app):
        self.app = app
        root = app.root
        self.win = w = tk.Toplevel(root)
        w.withdraw()
        w.overrideredirect(True)
        w.title("AskTim!")
        try:
            w.attributes("-topmost", True)
        except tk.TclError:
            pass
        big = app.scale >= 1.8
        self.platform = "mac" if sys.platform == "darwin" else "win" if os.name == "nt" else "x11"
        hard = self.platform != "mac"
        self.image = tk.PhotoImage(master=root, file=image_file(big, hard))
        if self.platform == "mac":
            bg = "systemTransparent"
            try:
                w.attributes("-transparent", True)
            except tk.TclError:
                bg = app.colors["window"]
        elif self.platform == "win":
            bg = KEY
            try:
                w.attributes("-transparentcolor", KEY)
            except tk.TclError:
                bg = app.colors["window"]
        else:
            bg = "#5ec6f5"  # (the blue of the picture's circle: only seen if the window cannot be cut to shape)
        try:
            w.config(bg=bg)
        except tk.TclError:
            bg = app.colors["window"]
            w.config(bg=bg)
        self.label = tk.Label(w, image=self.image, bd=0, highlightthickness=0, padx=0, pady=0, bg=bg, cursor="hand2")
        self.label.pack()
        self.shaped = False
        self._press = None
        self._moved = False
        self.busy = False
        self._bob = 0
        self._afters = {}
        for widget in (self.label,):
            widget.bind("<ButtonPress-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<ButtonRelease-1>", self._on_release)
            widget.bind("<Button-3>", self._on_menu)
            if self.platform == "mac":
                widget.bind("<Button-2>", self._on_menu)
                widget.bind("<Control-Button-1>", self._on_menu)
        self.menu = tk.Menu(w, tearoff=False)
        self._fill_menu()
        self.place(app.state.get("head"))
        w.deiconify()
        w.update_idletasks()
        if self.platform == "x11":
            self.shaped = _x11_shape(w, self.image)
            w.bind("<Map>", lambda e: _x11_shape(w, self.image))
        self._keep_on_top()

    # -- where it is ------------------------------------------------------------------------------

    def default_place(self):
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        return [sw - self.image.width() - 40, sh - self.image.height() - 110]

    def place(self, xy=None):
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = xy if (isinstance(xy, (list, tuple)) and len(xy) == 2) else self.default_place()
        try:
            x, y = int(x), int(y)
        except (TypeError, ValueError):
            x, y = self.default_place()
        # (a screen that was unplugged: back where it can be seen)
        if not (-self.image.width() // 2 < x < sw - 20 and 0 <= y < sh - 20):
            x, y = self.default_place()
        self.win.geometry("+%d+%d" % (x, y))

    def position(self):
        # (what was asked for last: the window system may not have moved the window yet)
        import re

        m = re.search(r"([+-]-?\d+)([+-]-?\d+)$", self.win.geometry())
        if m:
            return [int(m.group(1)), int(m.group(2))]
        return [self.win.winfo_x(), self.win.winfo_y()]

    def _keep_on_top(self):
        # X11 window managers do not stack a window that they do not manage: lift it now and then
        if self.platform == "x11":
            try:
                if self.win.winfo_viewable():
                    self.win.lift()
            except tk.TclError:
                return
            self._afters["top"] = self.win.after(3000, self._keep_on_top)

    def stop(self):
        """Nothing more is due later (the window is closing)."""
        self.busy = False
        for after_id in self._afters.values():
            try:
                self.win.after_cancel(after_id)
            except (tk.TclError, ValueError):
                pass
        self._afters.clear()

    # -- the mouse --------------------------------------------------------------------------------

    def _on_press(self, e):
        x, y = self.position()
        self._press = (e.x_root, e.y_root, x, y - self._bob, time.time())
        self._moved = False

    def _on_drag(self, e):
        if not self._press:
            return
        x0, y0, wx, wy, _t = self._press
        dx, dy = e.x_root - x0, e.y_root - y0
        if abs(dx) + abs(dy) > 4:
            self._moved = True
        if self._moved:
            self._bob = 0
            self.win.geometry("+%d+%d" % (wx + dx, wy + dy))

    def _on_release(self, e):
        if self._press is None:
            return
        self._press = None
        if self._moved:
            self.app.remember(head=self.position())
        else:
            self.app.toggle_chat()

    def _fill_menu(self):
        m = self.menu
        m.delete(0, "end")
        m.add_command(label="Chat", command=lambda: self.app.open_chat("chat"))
        m.add_command(label="Agent", command=lambda: self.app.open_chat("agent"))
        m.add_command(label="Settings…", command=self.app.open_settings)
        m.add_separator()
        m.add_command(label="Choose a folder…", command=self.app.choose_folder)
        m.add_command(label="Put me back in the corner", command=self._to_corner)
        m.add_separator()
        m.add_command(label="Quit AskTim!", command=self._quit)

    def _quit(self):
        # (once the menu is done: on Windows and macOS it is the system's own menu, and this is called from its handling)
        try:
            self.win.after_idle(self.app.quit)
        except tk.TclError:
            self.app.quit()

    def _on_menu(self, e):
        self._fill_menu()
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            try:
                self.menu.grab_release()
            except tk.TclError:
                pass

    def _to_corner(self):
        self.place(None)
        self.app.remember(head=self.position())

    # -- while AskTim! works, the head nods -----------------------------------------------------------

    def set_busy(self, busy):
        if busy and not self.busy:
            self.busy = True
            self._nod()
        elif not busy:
            self.busy = False

    def _nod(self):
        try:
            x, y = self.position()
            if not self.busy:
                if self._bob:
                    self.win.geometry("+%d+%d" % (x, y - self._bob))
                    self._bob = 0
                return
            if self._press is None:
                step = 3 if self._bob == 0 else -3
                self._bob += step
                self.win.geometry("+%d+%d" % (x, y + step))
            self._afters["nod"] = self.win.after(450, self._nod)
        except tk.TclError:
            pass
