"""A thin adapter over the macOS Accessibility API (AXUIElement).

Everything that touches pyobjc lives here, behind one small interface, so
the probe logic in focus.py is plain Python that the test suite drives
with a fake tree on any platform. Imports are lazy: importing this module
never imports pyobjc; AXBackend.create() does, and returns None when it
can't (not macOS, or pyobjc-framework-ApplicationServices missing).

Interface (the fake in tests/test_macos_focus.py implements the same):

    system_wide()                    -> element
    attribute(element, name)         -> value or None
    parameterized(element, name, p)  -> value or None
    settable(element, name)          -> bool
    set_attribute(element, name, v)  -> bool
    pid(element)                     -> int or None
    application(pid)                 -> element
    app_info(pid)                    -> (localized name, bundle id)
    rect(value)                      -> (x, y, w, h) or None   (an AXValue CGRect)
    point(value) / size(value)       -> (a, b) or None
    text_range(value)                -> (location, length) or None
    make_range(location, length)     -> an AXValue CFRange
    set_timeout(element, seconds)

Coordinates are global screen points with the origin at the top-left of
the primary display — the same space Quartz events and window lists use.
Requires Accessibility permission for the process (its responsible app).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# AXValueType numbers (AXValue.h), used when pyobjc lacks the constant.
_AX_VALUE_CGPOINT = 1
_AX_VALUE_CGSIZE = 2
_AX_VALUE_CGRECT = 3
_AX_VALUE_CFRANGE = 4
AX_ERROR_SUCCESS = 0
AX_ERROR_API_DISABLED = -25211  # kAXErrorAPIDisabled: no Accessibility permission


class AXBackend:
    def __init__(self, hi, appkit):
        self._hi = hi          # the ApplicationServices (HIServices) module
        self._appkit = appkit  # AppKit, for NSRunningApplication (may be None)
        self.last_error = AX_ERROR_SUCCESS

    @classmethod
    def create(cls) -> Optional["AXBackend"]:
        try:
            import ApplicationServices as hi  # pyobjc-framework-ApplicationServices
        except ImportError:
            try:
                import HIServices as hi  # older pyobjc layouts
            except ImportError:
                return None
        try:
            import AppKit as appkit
        except ImportError:
            appkit = None
        return cls(hi, appkit)

    def _const(self, name: str, default):
        return getattr(self._hi, name, default)

    def system_wide(self):
        return self._hi.AXUIElementCreateSystemWide()

    def application(self, pid: int):
        return self._hi.AXUIElementCreateApplication(int(pid))

    def set_timeout(self, element, seconds: float) -> None:
        try:
            self._hi.AXUIElementSetMessagingTimeout(element, float(seconds))
        except Exception:
            logger.debug("AXUIElementSetMessagingTimeout failed", exc_info=True)

    def attribute(self, element, name: str):
        if element is None:
            return None
        try:
            error, value = self._hi.AXUIElementCopyAttributeValue(element, name, None)
        except Exception:
            logger.debug("AX attribute %s failed", name, exc_info=True)
            return None
        self.last_error = int(error)
        return value if error == AX_ERROR_SUCCESS else None

    def parameterized(self, element, name: str, parameter):
        if element is None:
            return None
        try:
            error, value = self._hi.AXUIElementCopyParameterizedAttributeValue(
                element, name, parameter, None
            )
        except Exception:
            logger.debug("AX parameterized attribute %s failed", name, exc_info=True)
            return None
        self.last_error = int(error)
        return value if error == AX_ERROR_SUCCESS else None

    def settable(self, element, name: str) -> bool:
        if element is None:
            return False
        try:
            error, flag = self._hi.AXUIElementIsAttributeSettable(element, name, None)
        except Exception:
            return False
        return error == AX_ERROR_SUCCESS and bool(flag)

    def set_attribute(self, element, name: str, value) -> bool:
        if element is None:
            return False
        try:
            return self._hi.AXUIElementSetAttributeValue(element, name, value) == AX_ERROR_SUCCESS
        except Exception:
            return False

    def pid(self, element) -> Optional[int]:
        if element is None:
            return None
        try:
            error, pid = self._hi.AXUIElementGetPid(element, None)
        except Exception:
            return None
        return int(pid) if error == AX_ERROR_SUCCESS else None

    def app_info(self, pid: int) -> tuple[str, str]:
        if self._appkit is None:
            return "", ""
        try:
            app = self._appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(int(pid))
        except Exception:
            return "", ""
        if app is None:
            return "", ""
        return str(app.localizedName() or ""), str(app.bundleIdentifier() or "")

    def _unpack(self, value, kind_name: str, kind_default: int):
        if value is None:
            return None
        try:
            ok, unpacked = self._hi.AXValueGetValue(value, self._const(kind_name, kind_default), None)
        except Exception:
            return None
        return unpacked if ok else None

    def rect(self, value):
        rect = self._unpack(value, "kAXValueCGRectType", _AX_VALUE_CGRECT)
        if rect is None:
            return None
        return (float(rect.origin.x), float(rect.origin.y),
                float(rect.size.width), float(rect.size.height))

    def point(self, value):
        point = self._unpack(value, "kAXValueCGPointType", _AX_VALUE_CGPOINT)
        return None if point is None else (float(point.x), float(point.y))

    def size(self, value):
        size = self._unpack(value, "kAXValueCGSizeType", _AX_VALUE_CGSIZE)
        return None if size is None else (float(size.width), float(size.height))

    def text_range(self, value):
        found = self._unpack(value, "kAXValueCFRangeType", _AX_VALUE_CFRANGE)
        if found is None:
            return None
        return int(found.location), int(found.length)

    def make_range(self, location: int, length: int):
        kind = self._const("kAXValueCFRangeType", _AX_VALUE_CFRANGE)
        span = (int(location), int(length))
        try:
            from CoreFoundation import CFRangeMake  # pyobjc-framework-Cocoa

            span = CFRangeMake(*span)
        except Exception:
            pass  # pyobjc also takes a plain (location, length) tuple
        try:
            return self._hi.AXValueCreate(kind, span)
        except Exception:
            logger.debug("AXValueCreate(CFRange) failed", exc_info=True)
            return None
