"""macOS (beta) platform backends, selected automatically on darwin:

    injector.py     Quartz keystroke injection (create_injector)
    hotkey.py       the event-tap hotkey listener (create_hotkey_listener)
    keylayout.py    which key types a character on the current layout
    ax.py, focus.py the focused app and field through the Accessibility
                    API (focusprobe.probe_focus / probe_selection)
    permissions.py  Accessibility, Input Monitoring, Microphone: checks,
                    the fix in plain words (voice-keyboard doctor)
    overlay.py      recording feedback until there is a native overlay

Every pyobjc import is lazy, so importing this package never needs a Mac.
MACOS.md at the repo root has the plan and what is untested on hardware.
"""
