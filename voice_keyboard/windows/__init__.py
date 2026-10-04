"""Windows platform layer — pure ctypes, no extra dependencies.

- injector: SendInput keystrokes (Unicode packets; Enter/Tab as real keys)
- hotkey: a low-level keyboard hook (hold-to-talk, bare Right Ctrl)
- clipboard / selection: the native clipboard, and copy-the-selection
- shell: the overlay pill, the Kai orb, and the tray icon
- app: the windowless tray app that hosts the daemon
  (`pythonw -m voice_keyboard.windows`)

The injector/hotkey factories select these automatically on win32.
"""
