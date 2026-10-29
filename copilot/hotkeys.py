"""Global hotkeys (hooks run on their own thread; hand off to the loop).

Windows uses the `keyboard` library. Linux (X11 only, not Wayland) and macOS use
pynput; macOS asks for the Accessibility permission on first use.
"""

import sys

from . import config

WINDOW_TITLE = "Meeting Copilot"


def focus_window():
    """Bring the app window to the front. Windows only; elsewhere the box is just focused."""
    if sys.platform != "win32":
        return
    import win32com.client
    import win32gui

    hwnds = []
    win32gui.EnumWindows(
        lambda h, _: hwnds.append(h) if WINDOW_TITLE in win32gui.GetWindowText(h) else None, None
    )
    if hwnds:
        # Windows refuses SetForegroundWindow from a background process unless an
        # input event just happened; a synthetic Alt press satisfies that rule.
        win32com.client.Dispatch("WScript.Shell").SendKeys("%")
        win32gui.SetForegroundWindow(hwnds[0])


def to_pynput(combo):
    """"ctrl+alt+space" -> "<ctrl>+<alt>+<space>" (single characters stay bare)."""
    return "+".join(k if len(k) == 1 else f"<{k}>" for k in combo.split("+"))


def _bind(bindings):
    """bindings: {"ctrl+alt+1": callback}"""
    if sys.platform == "win32":
        import keyboard

        for combo, fn in bindings.items():
            keyboard.add_hotkey(combo, fn)
        return
    from pynput import keyboard

    keyboard.GlobalHotKeys({to_pynput(combo): fn for combo, fn in bindings.items()}).start()


def register(loop, assistant, emit):
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
    try:
        _bind(bindings)
    except Exception as e:  # no X server, Wayland, missing permission ...: the buttons still work
        print(f"Hotkeys:  unavailable ({e}); use the buttons in the window")
