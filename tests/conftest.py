import sys
from types import ModuleType

# Provide a minimal evdev stub so tests can import the injector module
# without needing Linux uinput bindings in the test environment.
if "evdev" not in sys.modules:
    evdev_stub = ModuleType("evdev")

    class _Ecodes(ModuleType):
        # Unknown KEY_/EV_/BTN_ names get a stable synthetic code on first
        # access, so new keycodes in the injector never break collection.
        _next = 1000

        def __getattr__(self, name):
            if name.startswith(("KEY_", "EV_", "BTN_")):
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
