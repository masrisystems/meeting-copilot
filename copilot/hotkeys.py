"""Global hotkeys (keyboard library hooks run on their own thread; hand off to the loop)."""

import keyboard

from . import config

WINDOW_TITLE = "Meeting Copilot"


def focus_window():
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


def register(loop, assistant, emit):
    def on_loop(fn, *args):
        return lambda: loop.call_soon_threadsafe(fn, *args)

    def ask(query):
        loop.call_soon_threadsafe(lambda: loop.create_task(assistant.ask(query)))

    for preset in config.PRESETS:
        keyboard.add_hotkey(preset["hotkey"], ask, args=(preset["query"],))

    def focus():
        focus_window()
        loop.call_soon_threadsafe(emit, {"type": "focus_query"})

    keyboard.add_hotkey(config.HOTKEY_FOCUS, focus)
    keyboard.add_hotkey(config.HOTKEY_TRANSLATE,
                        on_loop(lambda: assistant.set_mode(translate=not assistant.translate)))
    keyboard.add_hotkey(config.HOTKEY_PROACTIVE,
                        on_loop(lambda: assistant.set_mode(proactive=not assistant.proactive)))
