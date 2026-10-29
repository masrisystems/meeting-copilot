"""Always-on-top and opacity for the app window (replaces WindowTop). Persisted in settings.json.

Windows: Win32 API on the window found by its title. Linux: X11 window-manager hints
(_NET_WM_STATE_ABOVE, _NET_WM_WINDOW_OPACITY), not Wayland. macOS: not supported, the
controls are hidden.
"""

import json
import os
import sys

from . import config

TITLE = "Meeting Copilot"
SETTINGS_FILE = config.ROOT / "settings.json"
DEFAULTS = {"on_top": True, "opacity": 1.0}


def supported():
    if sys.platform == "win32":
        return True
    return sys.platform.startswith("linux") and bool(os.environ.get("DISPLAY")) \
        and os.environ.get("XDG_SESSION_TYPE") != "wayland"


def find_hwnds():
    import win32gui

    hwnds = []
    win32gui.EnumWindows(lambda h, _: hwnds.append(h) if win32gui.GetWindowText(h) == TITLE else None, None)
    return hwnds


def _apply_win32(on_top, opacity):
    import win32con
    import win32gui

    hwnds = find_hwnds()
    for h in hwnds:
        win32gui.SetWindowPos(h, win32con.HWND_TOPMOST if on_top else win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        style = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(h, win32con.GWL_EXSTYLE, style | win32con.WS_EX_LAYERED)
        win32gui.SetLayeredWindowAttributes(h, 0, round(255 * opacity), win32con.LWA_ALPHA)
    return bool(hwnds)


def _apply_x11(on_top, opacity):
    from Xlib import X, Xatom, display
    from Xlib.protocol import event

    d = display.Display()
    try:
        root = d.screen().root
        atom = d.intern_atom
        found = False
        for wid in root.get_full_property(atom("_NET_CLIENT_LIST"), X.AnyPropertyType).value:
            w = d.create_resource_object("window", wid)
            name = w.get_full_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"))
            if not name or name.value.decode("utf-8", "replace") != TITLE:
                continue
            found = True
            # The window manager owns the stacking: ask it via a client message on the root window.
            msg = event.ClientMessage(window=w, client_type=atom("_NET_WM_STATE"),
                                      data=(32, [int(on_top), atom("_NET_WM_STATE_ABOVE"), 0, 1, 0]))
            root.send_event(msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            if opacity >= 1:
                w.delete_property(atom("_NET_WM_WINDOW_OPACITY"))
            else:
                w.change_property(atom("_NET_WM_WINDOW_OPACITY"), Xatom.CARDINAL, 32, [int(opacity * 0xFFFFFFFF)])
        d.flush()
        return found
    finally:
        d.close()


class Window:
    def __init__(self):
        self.supported = supported()
        self.settings = dict(DEFAULTS)
        try:
            self.settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def state(self):
        return {"type": "window", "supported": self.supported, **self.settings}

    def set(self, on_top=None, opacity=None):
        if on_top is not None:
            self.settings["on_top"] = bool(on_top)
        if opacity is not None:
            self.settings["opacity"] = min(1.0, max(0.4, float(opacity)))
        SETTINGS_FILE.write_text(json.dumps(self.settings), encoding="utf-8")
        self.apply()

    def apply(self):
        """Blocking (X11 round trips); call via asyncio.to_thread. Returns False if no window was found."""
        if not self.supported:
            return False
        try:
            fn = _apply_win32 if sys.platform == "win32" else _apply_x11
            return fn(self.settings["on_top"], self.settings["opacity"])
        except Exception as e:
            print(f"Window:   could not set on-top/opacity ({e})")
            return False
