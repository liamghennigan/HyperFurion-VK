"""The Windows tray menu in setup mode (no key yet).

The hosted subscription isn't on sale, so the tray must not steer a new
user to it: the settings file (a key, or your own server) comes first, and
the sign-in item says it is for existing subscribers. The Win32 calls are
stubbed; only the labels and their order are checked.
"""

from unittest import mock

from voice_keyboard.windows import shell as shell_mod
from voice_keyboard.windows.shell import ShellCallbacks, WinShell


def _menu_labels(monkeypatch, setup_message: str) -> list:
    user32 = mock.Mock()
    user32.TrackPopupMenu.return_value = 0  # dismissed: nothing chosen
    monkeypatch.setattr(shell_mod, "_load_api", lambda: (user32, None, None, None, None))
    shell = WinShell(ShellCallbacks(), version="2.4.0")
    shell._setup_message = setup_message
    monkeypatch.setattr(shell, "_focusable", lambda hwnd: False)
    monkeypatch.setattr(shell, "_app_window", lambda hwnd: None)
    shell._show_menu()
    return [c.args[3] for c in user32.AppendMenuW.call_args_list if c.args[3]]


def test_setup_menu_offers_settings_before_hosted_sign_in(monkeypatch):
    labels = _menu_labels(monkeypatch, "Add your speech provider API key to get started")
    settings = labels.index("Open settings file…")
    sign_in = next(i for i, text in enumerate(labels) if text.startswith("Sign in"))
    assert settings < sign_in
    assert "existing hosted-service subscribers" in labels[sign_in]
    assert not any("Sign in to the hosted service" in text for text in labels)


def test_ready_menu_has_no_sign_in(monkeypatch):
    labels = _menu_labels(monkeypatch, "")
    assert not any(text.startswith("Sign in") for text in labels)
