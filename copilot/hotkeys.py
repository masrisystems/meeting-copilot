"""Global hotkeys (hooks run on their own thread; hand off to the loop).

Windows uses RegisterHotKey: a low-level hook (the `keyboard` library) is silently removed
by Windows when the busy process answers it too slowly, which killed the hotkeys mid-meeting.
Linux (X11 only, not Wayland) and macOS use pynput; macOS asks for the Accessibility
permission on first use.
"""

import sys
import threading

from . import config, window


def focus_window():
    """Bring the app window to the front. Windows only; elsewhere the box is just focused."""
    if sys.platform != "win32":
        return
    import win32com.client
    import win32gui

    hwnds = window.find_hwnds()
    if hwnds:
        # Windows refuses SetForegroundWindow from a background process unless an
        # input event just happened; a synthetic Alt press satisfies that rule.
        win32com.client.Dispatch("WScript.Shell").SendKeys("%")
        win32gui.SetForegroundWindow(hwnds[0])


def to_pynput(combo):
    """"ctrl+alt+space" -> "<ctrl>+<alt>+<space>" (single characters stay bare)."""
    return "+".join(k if len(k) == 1 else f"<{k}>" for k in combo.split("+"))


MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
VIRTUAL_KEYS = {"space": 0x20}


def to_win32(combo):
    """"ctrl+alt+t" -> (MOD_CONTROL | MOD_ALT, ord("T")) for RegisterHotKey."""
    *mods, key = combo.lower().split("+")
    vk = VIRTUAL_KEYS.get(key) or ord(key.upper())
    flags = 0
    for mod in mods:
        flags |= MODIFIERS[mod]
    return flags, vk


def _bind_win32(bindings):
    """WM_HOTKEY arrives on the thread that registered the hotkeys, so it runs the message loop."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    callbacks = list(bindings.items())

    def run():
        failed = []
        for i, (combo, _) in enumerate(callbacks):
            mods, vk = to_win32(combo)
            if not user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                failed.append(combo)
        if failed:  # usually taken by another app
            print(f"Hotkeys:  {', '.join(failed)} unavailable (in use by another app?); use the buttons")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                try:
                    callbacks[msg.wParam][1]()
                except Exception as e:  # one failing action must not end the loop for all hotkeys
                    print(f"Hotkey {callbacks[msg.wParam][0]} failed: {e}")

    threading.Thread(target=run, name="hotkeys", daemon=True).start()


def _bind(bindings):
    """bindings: {"ctrl+alt+1": callback}"""
    if sys.platform == "win32":
        _bind_win32(bindings)
        return
    from pynput import keyboard

    keyboard.GlobalHotKeys({to_pynput(combo): fn for combo, fn in bindings.items()}).start()


def register(loop, assistant, emit, win):
    def on_loop(fn, *args):
        return lambda: loop.call_soon_threadsafe(fn, *args)

    def ask(query):
        return lambda: loop.call_soon_threadsafe(lambda: loop.create_task(assistant.ask(query)))

    def focus():
        focus_window()
        loop.call_soon_threadsafe(emit, {"type": "focus_query"})

    bindings = {preset["hotkey"]: ask(preset["query"]) for preset in config.PRESETS}
    bindings[config.HOTKEY_FOCUS] = focus
    bindings[config.HOTKEY_TRANSLATE] = on_loop(lambda: assistant.set_mode(translate=not assistant.translate))
    bindings[config.HOTKEY_PROACTIVE] = on_loop(lambda: assistant.set_mode(proactive=not assistant.proactive))
    bindings[config.HOTKEY_MIC] = on_loop(lambda: assistant.set_mic(not assistant.mic_on))
    if win.supported:
        bindings[config.HOTKEY_ON_TOP] = on_loop(lambda: (win.set(on_top=not win.settings["on_top"]),
                                                         emit(win.state())))
    try:
        _bind(bindings)
    except Exception as e:  # no X server, Wayland, missing permission ...: the buttons still work
        print(f"Hotkeys:  unavailable ({e}); use the buttons in the window")
