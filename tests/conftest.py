import sys
from types import ModuleType

# Provide a minimal evdev stub so tests can import the injector module
# without needing Linux uinput bindings in the test environment.
if "evdev" not in sys.modules:
    evdev_stub = ModuleType("evdev")

    import re

    # Real evdev key names (linux/input-event-codes.h) beyond the explicit
    # list below. Only these get synthesized: a stub that invents a code for
    # ANY KEY_* name would make a typo'd hotkey binding impossible to reject.
    _EVDEV_WORDS = (
        "ESC MINUS EQUAL BACKSPACE TAB LEFTBRACE RIGHTBRACE ENTER LEFTCTRL "
        "SEMICOLON APOSTROPHE GRAVE LEFTSHIFT BACKSLASH COMMA DOT SLASH "
        "RIGHTSHIFT LEFTALT SPACE CAPSLOCK NUMLOCK SCROLLLOCK SYSRQ RIGHTCTRL "
        "RIGHTALT LINEFEED HOME UP PAGEUP LEFT RIGHT END DOWN PAGEDOWN INSERT "
        "DELETE MUTE VOLUMEDOWN VOLUMEUP POWER PAUSE LEFTMETA RIGHTMETA "
        "COMPOSE MENU PRINT STOP AGAIN UNDO COPY PASTE FIND CUT HELP CALC "
        "SLEEP WAKEUP MAIL BOOKMARKS COMPUTER BACK FORWARD HOMEPAGE REFRESH "
        "PLAYPAUSE NEXTSONG PREVIOUSSONG STOPCD RECORD MICMUTE ZENKAKUHANKAKU "
        "102ND RO KATAKANA HIRAGANA HENKAN MUHENKAN YEN HANGEUL HANJA"
    ).split()
    _EVDEV_NAME = re.compile(
        r"^(?:KEY_(?:[A-Z]|[0-9]|F(?:[1-9]|1[0-9]|2[0-4])|KP[A-Z0-9]+|"
        + "|".join(_EVDEV_WORDS)
        + r")|BTN_[A-Z0-9_]+|EV_[A-Z_]+)$"
    )

    class _Ecodes(ModuleType):
        # Real-but-unlisted KEY_/EV_/BTN_ names get a stable synthetic code
        # on first access, so new keycodes in the injector never break
        # collection; names evdev doesn't have raise like the real module.
        _next = 1000

        def __getattr__(self, name):
            if _EVDEV_NAME.match(name):
                _Ecodes._next += 1
                setattr(self, name, _Ecodes._next)
                return getattr(self, name)
            raise AttributeError(name)

    ecodes_stub = _Ecodes("ecodes")

    _keys = [
        "EV_KEY",
        "KEY_A", "KEY_B", "KEY_C", "KEY_D", "KEY_E", "KEY_F", "KEY_G",
        "KEY_H", "KEY_I", "KEY_J", "KEY_K", "KEY_L", "KEY_M", "KEY_N",
        "KEY_O", "KEY_P", "KEY_Q", "KEY_R", "KEY_S", "KEY_T", "KEY_U",
        "KEY_V", "KEY_W", "KEY_X", "KEY_Y", "KEY_Z",
        "KEY_0", "KEY_1", "KEY_2", "KEY_3", "KEY_4", "KEY_5", "KEY_6",
        "KEY_7", "KEY_8", "KEY_9",
        "KEY_SPACE", "KEY_MINUS", "KEY_EQUAL",
        "KEY_LEFTBRACE", "KEY_RIGHTBRACE", "KEY_BACKSLASH",
        "KEY_SEMICOLON", "KEY_APOSTROPHE", "KEY_COMMA", "KEY_DOT",
        "KEY_SLASH", "KEY_GRAVE", "KEY_ENTER", "KEY_TAB",
        "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_BACKSPACE",
        "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTALT", "KEY_RIGHTALT",
        "KEY_LEFTMETA", "KEY_RIGHTMETA",
    ]
    for value, name in enumerate(_keys):
        setattr(ecodes_stub, name, value)

    class _UInput:
        def __init__(self, *args, **kwargs):
            pass

        def write(self, *args, **kwargs):
            pass

        def syn(self):
            pass

        def close(self):
            pass

    evdev_stub.UInput = _UInput
    evdev_stub.InputDevice = object
    evdev_stub.list_devices = lambda: []
    evdev_stub.ecodes = ecodes_stub
    sys.modules["evdev"] = evdev_stub
    sys.modules["evdev.ecodes"] = ecodes_stub

# Provide a minimal pyaudio stub so tests can import the audio module
# without needing a real PortAudio build in CI.
if "pyaudio" not in sys.modules:
    pyaudio_stub = ModuleType("pyaudio")
    pyaudio_stub.paInt16 = 2

    class _Stream:
        def read(self, *args, **kwargs):
            return b"\x00" * 320

        def stop_stream(self):
            pass

        def close(self):
            pass

    class _PyAudio:
        def get_default_input_device_info(self):
            return {"index": 0, "name": "default", "maxInputChannels": 1}

        def get_device_count(self):
            return 0

        def get_device_info_by_index(self, index):
            return {}

        def open(self, *args, **kwargs):
            return _Stream()

        def terminate(self):
            pass

    pyaudio_stub.PyAudio = _PyAudio
    pyaudio_stub.Stream = _Stream
    sys.modules["pyaudio"] = pyaudio_stub


# ------------------------------------------------------------- no network

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_internet(monkeypatch):
    """Unit tests never reach a real service: a request to anything but a
    local server fails the test that made it (a dummy key sent to a live
    API is slow, flaky, and not what any test means to check)."""
    import urllib.parse

    import requests.adapters

    real_send = requests.adapters.HTTPAdapter.send
    reached: list[str] = []

    def send(self, request, *args, **kwargs):
        host = urllib.parse.urlsplit(request.url).hostname or ""
        if host not in {"localhost", "127.0.0.1", "::1"}:
            reached.append(host)
            # Code under test may swallow this; the teardown below won't.
            raise requests.exceptions.ConnectionError(f"tests may not reach {host}")
        return real_send(self, request, *args, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    yield
    assert not reached, f"test tried to reach {sorted(set(reached))}: mock the client instead"


@pytest.fixture(autouse=True)
def kai_notices(monkeypatch, tmp_path_factory):
    """Kai's on/off notices (assistant/announce.py) never reach the real
    desktop or the real state folder: the record lives in a temp folder and
    the notice is collected, not shown. Tests of the notices patch these
    themselves."""
    from voice_keyboard.assistant import announce

    folder = tmp_path_factory.mktemp("kai-notice")
    shown: list = []

    def show(title, body):
        shown.append((title, body))
        return True

    monkeypatch.setattr(announce, "record_path", lambda: folder / announce.RECORD_NAME)
    monkeypatch.setattr(announce, "show", show)
    yield shown
