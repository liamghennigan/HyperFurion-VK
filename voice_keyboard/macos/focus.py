"""The focused app and text field on macOS, through the Accessibility API.

What the Linux AT-SPI probe and the Win32 probe answer, for macOS: which
app has focus, whether the focused widget is a password field, where the
caret is (for the overlay), plus what the window title says and whether
the field is single- or multi-line (for line-break policy), and whether
it is a terminal — Terminal.app, iTerm2, Warp, Ghostty, kitty, Alacritty,
WezTerm and friends by bundle id, and the xterm.js terminal inside VS Code,
Cursor and Windsurf by the focused widget itself.

All of it is plain logic over the small interface in ax.py, so the tests
drive it with a fake accessibility tree. Every lookup is best-effort: a
missing attribute leaves its field at the "unknown" default, and None
means the focused app couldn't be found at all (no Accessibility
permission, or nothing focused).

Window titles come from the window's AXTitle, which needs only the
Accessibility permission — never from the window list's kCGWindowName,
which macOS blanks without Screen Recording permission.
"""

import dataclasses
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# How long one AX request may block on an unresponsive app (seconds).
MESSAGING_TIMEOUT_S = 0.25

# Terminals by bundle id: names are localized and can be renamed, ids not.
MAC_TERMINAL_BUNDLES = frozenset({
    "com.apple.Terminal",
    "com.googlecode.iterm2",
    "dev.warp.Warp-Stable", "dev.warp.Warp-Preview", "dev.warp.Warp",
    "com.mitchellh.ghostty",
    "net.kovidgoyal.kitty",
    "org.alacritty", "io.alacritty",
    "com.github.wez.wezterm",
    "co.zeit.hyper",
    "org.tabby",
    "com.raphaelamorim.rio",
})

# Editors with a built-in xterm.js terminal: there, the focused WIDGET
# decides (the editor pane is prose/code, the terminal pane is a shell).
MAC_IDE_BUNDLES = frozenset({
    "com.microsoft.VSCode", "com.microsoft.VSCodeInsiders",
    "com.vscodium", "com.vscodium.VSCodiumInsiders",
    "com.todesktop.230313mzl4w4u92",  # Cursor
    "com.exafunction.windsurf",
})

# Electron apps keep their accessibility tree off until an assistive app
# asks for it with AXManualAccessibility (electronjs.org/docs/latest/
# tutorial/accessibility). Asked once per process, only for these.
ELECTRON_BUNDLES = MAC_IDE_BUNDLES | frozenset({
    "com.tinyspeck.slackmacgap",  # Slack
    "com.hnc.Discord",
    "com.microsoft.teams2",
    "org.whispersystems.signal-desktop",
    "im.riot.app",  # Element
    "md.obsidian",
    "notion.id",
    "com.mattermost.desktop",
    "co.zeit.hyper",
    "org.tabby",
})

SINGLE_LINE_ROLES = frozenset({"AXTextField", "AXComboBox", "AXSecureTextField", "AXSearchField"})
MULTI_LINE_ROLES = frozenset({"AXTextArea"})
TEXT_ROLES = SINGLE_LINE_ROLES | MULTI_LINE_ROLES
SECURE = "AXSecureTextField"

# AX roles in the vocabulary the registers already speak (AT-SPI role
# names): "password text" forces verbatim, "terminal" the terminal register.
_SHARED_ROLES = {
    "AXTextArea": "text",
    "AXTextField": "entry",
    "AXComboBox": "combo box",
    "AXSearchField": "entry",
    "AXWebArea": "document web",
}


@dataclass(frozen=True)
class MacFocus:
    app: str = ""            # localized app name ("Terminal", "Code", "Slack")
    bundle_id: str = ""      # "com.apple.Terminal"
    pid: int = 0
    role: str = ""           # shared vocabulary: "password text", "terminal", "text", ...
    ax_role: str = ""        # the raw AXRole ("AXTextArea")
    ax_subrole: str = ""
    x: int = -1              # caret (else field) anchor, global top-left points
    y: int = -1
    editable: bool = False
    secret: bool = False     # a password field: never remembered, verbatim
    multiline: Optional[bool] = None  # True: text area; False: single-line field
    window_title: str = ""
    terminal: bool = False


# Electron processes already asked to build their accessibility tree.
_manual_accessibility_pids: set[int] = set()


def _text(value) -> str:
    try:
        return str(value) if value is not None else ""
    except Exception:
        return ""


def _class_list(ax, element) -> list[str]:
    """Chromium's AXDOMClassList (the web element's CSS classes)."""
    value = ax.attribute(element, "AXDOMClassList")
    if value is None:
        return []
    try:
        return [str(item) for item in value]
    except TypeError:
        return [str(value)]


def is_terminal_widget(ax, element, bundle_id: str) -> bool:
    """The focused widget is a terminal even though the app is not one: an
    xterm.js terminal (VS Code's, Cursor's, a browser shell's) is a hidden
    textarea with the class xterm-helper-textarea; in an IDE, the terminal
    pane's input is also labelled "Terminal …"."""
    if element is None:
        return False
    if any("xterm-helper-textarea" in item for item in _class_list(ax, element)):
        return True
    if bundle_id in MAC_IDE_BUNDLES:
        for name in ("AXDescription", "AXTitle", "AXRoleDescription", "AXHelp"):
            label = _text(ax.attribute(element, name)).strip().lower()
            if label.startswith("terminal"):
                return True
    return False


def _usable(rect) -> bool:
    if rect is None:
        return False
    x, y, width, height = rect
    return height > 0 and x > -30000 and y > -30000 and (width > 0 or height > 0)


def caret_anchor(ax, element) -> Optional[tuple[int, int]]:
    """The caret's top-left in global points: the bounds of the empty range
    at the caret, else the character before it (its right edge)."""
    found = ax.text_range(ax.attribute(element, "AXSelectedTextRange"))
    if found is None:
        return None
    location, _length = found
    for start, length, right_edge in ((location, 0, False), (location - 1, 1, True), (location, 1, False)):
        if start < 0:
            continue
        span = ax.make_range(start, length)
        if span is None:
            continue
        rect = ax.rect(ax.parameterized(element, "AXBoundsForRange", span))
        if _usable(rect):
            x, y, width, _height = rect
            return int(x + width) if right_edge else int(x), int(y)
    return None


def element_anchor(ax, element) -> Optional[tuple[int, int]]:
    """The middle of the element's top edge (the overlay's fallback)."""
    position = ax.point(ax.attribute(element, "AXPosition"))
    size = ax.size(ax.attribute(element, "AXSize"))
    if position is None or size is None or size[0] <= 0 or size[1] <= 0:
        return None
    return int(position[0] + max(size[0] // 2, 1)), int(position[1])


def _shared_role(ax_role: str, *, secret: bool, terminal: bool) -> str:
    if secret:
        return "password text"
    if terminal:
        return "terminal"
    if ax_role in _SHARED_ROLES:
        return _SHARED_ROLES[ax_role]
    return ax_role[2:].lower() if ax_role.startswith("AX") else ax_role.lower()


def _focused_element(ax, system, app_element, pid: int, bundle_id: str):
    element = ax.attribute(app_element, "AXFocusedUIElement")
    if element is None:
        element = ax.attribute(system, "AXFocusedUIElement")
    if (
        bundle_id in ELECTRON_BUNDLES
        and pid not in _manual_accessibility_pids
        and (element is None or _text(ax.attribute(element, "AXRole")) not in TEXT_ROLES)
    ):
        # An Electron app with its tree switched off: ask once, look again.
        _manual_accessibility_pids.add(pid)
        if ax.set_attribute(app_element, "AXManualAccessibility", True):
            logger.info("Asked %s to expose its accessibility tree", bundle_id)
            element = ax.attribute(app_element, "AXFocusedUIElement") or element
    return element


def probe(ax, *, own_pid: Optional[int] = None) -> Optional[MacFocus]:
    """The focused app and widget, or None when no app could be found."""
    system = ax.system_wide()
    ax.set_timeout(system, MESSAGING_TIMEOUT_S)
    app_element = ax.attribute(system, "AXFocusedApplication")
    if app_element is None:
        return None
    pid = ax.pid(app_element) or 0
    if pid and pid == (os.getpid() if own_pid is None else own_pid):
        return None  # our own window (a menu, the overlay) is not a target
    name, bundle_id = ax.app_info(pid) if pid else ("", "")
    if not name:
        name = _text(ax.attribute(app_element, "AXTitle"))
    if not name:
        return None

    window = ax.attribute(app_element, "AXFocusedWindow")
    window_title = _text(ax.attribute(window, "AXTitle")) if window is not None else ""

    element = _focused_element(ax, system, app_element, pid, bundle_id)
    ax_role = _text(ax.attribute(element, "AXRole")) if element is not None else ""
    ax_subrole = _text(ax.attribute(element, "AXSubrole")) if element is not None else ""
    secret = SECURE in (ax_role, ax_subrole)
    terminal = bundle_id in MAC_TERMINAL_BUNDLES or is_terminal_widget(ax, element, bundle_id)

    if ax_role in MULTI_LINE_ROLES:
        multiline: Optional[bool] = True
    elif ax_role in SINGLE_LINE_ROLES or secret:
        multiline = False
    else:
        multiline = None
    editable = bool(element is not None and (
        ax_role in TEXT_ROLES or secret or ax.settable(element, "AXValue")
    ))

    anchor = None
    if element is not None:
        if not secret:
            # A secure field's caret bounds would reveal its length.
            anchor = caret_anchor(ax, element)
        anchor = anchor or element_anchor(ax, element)
    x, y = anchor if anchor is not None else (-1, -1)

    return MacFocus(
        app=name,
        bundle_id=bundle_id,
        pid=pid,
        role=_shared_role(ax_role, secret=secret, terminal=terminal),
        ax_role=ax_role,
        ax_subrole=ax_subrole,
        x=x,
        y=y,
        editable=editable,
        secret=secret,
        multiline=multiline,
        window_title=window_title,
        terminal=terminal,
    )


def selected_text(ax, limit: int, *, editable_only: bool = True,
                  allow_terminal: bool = False) -> Optional[tuple[str, int]]:
    """On demand only: the focused widget's selection and its length —
    ("", n) when it is longer than `limit`, ("", 0) when there is none,
    never from a password field; None when the tree can't be asked.

    For a rewrite ("VK, make this shorter" with nothing dictated) the
    defaults hold: an editable field only (typing replaces the selection),
    never a terminal (typing does not replace a terminal's selection). Read
    aloud passes editable_only=False and allow_terminal=True: reading has
    no side effects, and a web page's text is not editable."""
    system = ax.system_wide()
    ax.set_timeout(system, MESSAGING_TIMEOUT_S)
    app_element = ax.attribute(system, "AXFocusedApplication")
    if app_element is None:
        return None
    pid = ax.pid(app_element) or 0
    _name, bundle_id = ax.app_info(pid) if pid else ("", "")
    element = ax.attribute(app_element, "AXFocusedUIElement")
    if element is None:
        return None
    ax_role = _text(ax.attribute(element, "AXRole"))
    ax_subrole = _text(ax.attribute(element, "AXSubrole"))
    if SECURE in (ax_role, ax_subrole):
        return "", 0
    terminal = bundle_id in MAC_TERMINAL_BUNDLES or is_terminal_widget(ax, element, bundle_id)
    if terminal and not allow_terminal:
        return "", 0
    if editable_only and ax_role not in TEXT_ROLES and not ax.settable(element, "AXValue"):
        return "", 0
    found = ax.text_range(ax.attribute(element, "AXSelectedTextRange"))
    length = found[1] if found is not None else 0
    if length <= 0:
        return "", 0
    if length > limit:
        return "", length
    text = _text(ax.attribute(element, "AXSelectedText"))
    return text[:limit], length


def to_focus_info(found: MacFocus, focus_cls):
    """A MacFocus as the shared FocusInfo dataclass. Fields FocusInfo has
    today are filled; richer ones (window title, single/multi-line, bundle
    id) are filled only if FocusInfo grows a field of one of these names,
    so this keeps working as that dataclass evolves."""
    extras = {
        "title": found.window_title,
        "window_title": found.window_title,
        "multiline": found.multiline,
        "multi_line": found.multiline,
        "single_line": None if found.multiline is None else not found.multiline,
        "bundle_id": found.bundle_id,
        "terminal": found.terminal,
    }
    names = {field.name for field in dataclasses.fields(focus_cls)}
    kwargs = {
        "app": found.app,
        "role": found.role,
        "x": found.x,
        "y": found.y,
        "editable": found.editable,
        "secret": found.secret,
    }
    kwargs = {key: value for key, value in kwargs.items() if key in names}
    for key, value in extras.items():
        if key in names and value is not None:
            kwargs[key] = value
    return focus_cls(**kwargs)


def probe_focus_info(focus_cls):
    """The focusprobe entry point: a FocusInfo, or None (no backend, no
    permission, nothing focused). Never raises."""
    try:
        from voice_keyboard.macos.ax import AXBackend

        ax = AXBackend.create()
        if ax is None:
            return None
        found = probe(ax)
        return None if found is None else to_focus_info(found, focus_cls)
    except Exception:
        logger.debug("macOS accessibility probe failed", exc_info=True)
        return None


def probe_selection(limit: int, **options) -> Optional[tuple[str, int]]:
    """The focusprobe.probe_selection entry point on macOS (options as for
    selected_text). Never raises."""
    try:
        from voice_keyboard.macos.ax import AXBackend

        ax = AXBackend.create()
        return None if ax is None else selected_text(ax, limit, **options)
    except Exception:
        logger.debug("macOS selection probe failed", exc_info=True)
        return None
