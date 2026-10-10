"""What a spoken line break presses in the field you are dictating into.

Every line break dictation types ("new line", "new paragraph", a new
bullet, number, checkbox or heading) is a key press, and in many fields
Enter does something else: it runs the line in a terminal, sends the
message in a chat, submits a search box or a form. So a session decides,
before it types anything, what a line break is in the focused field, in
this order:

1. A terminal, or focus that couldn't be identified: nothing. Enter is
   never pressed there; the injector types a space instead.
2. A one-line field (a search box, a form input, an address bar):
   nothing, for the same reason. Enter would submit it.
3. Your own rules: ``[registers.newline]`` (a site beats a page title,
   which beats an app) and ``[registers] chat_apps`` (shorthand for
   "shift+enter").
4. A desktop chat app (Slack, Discord, Teams, Signal, ChatGPT, Claude,
   ...): Shift+Enter.
5. A spreadsheet (Excel, LibreOffice Calc; Google Sheets and other web
   sheets): nothing. Enter and Shift+Enter commit the cell and move the
   selection, so the rest of the dictation would replace the next cell.
6. A web browser: a known chat site gets Shift+Enter; a known document
   editor (Google Docs, Notion, Word, Confluence, ...) gets Enter, so
   paragraphs and lists keep working; any other page gets Shift+Enter,
   which is a plain line break in a text box, a soft break in a rich
   editor, and never a send.
7. Any other identified app: Enter.

Pure logic: no I/O. The window title and page address can say what you
are reading, so nothing here logs them; a choice names only the built-in
site or the config key that matched.
"""

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional
from urllib.parse import urlsplit

from voice_keyboard.flow.registers import TERMINAL, is_chat_app, register_for_app

ENTER = "enter"
SHIFT_ENTER = "shift+enter"
NONE = "none"
KEYS = (ENTER, SHIFT_ENTER, NONE)

# How config may spell each key (case and spaces don't matter).
_KEY_ALIASES = {
    "enter": ENTER, "return": ENTER,
    "shift+enter": SHIFT_ENTER, "shift-enter": SHIFT_ENTER, "shiftenter": SHIFT_ENTER,
    "shift+return": SHIFT_ENTER, "shift-return": SHIFT_ENTER,
    "none": NONE, "nothing": NONE, "off": NONE,
}


def normalize_key(value) -> Optional[str]:
    """A config value as one of KEYS, or None when it is not one."""
    if not isinstance(value, str):
        return None
    return _KEY_ALIASES.get(re.sub(r"\s+", "", value.lower()))


# Web browsers, by focused application: AT-SPI application names (Linux),
# app names (macOS) and exe basenames without ".exe" (Windows), lowercase.
# In a browser the app name says nothing about the page, so the window
# title and the page address decide; the one place this list lives.
BROWSER_APPS = frozenset({
    # Chromium family
    "google chrome", "google-chrome", "google-chrome-stable", "google-chrome-beta",
    "google-chrome-unstable", "google chrome beta", "google chrome dev",
    "google chrome canary", "chrome", "chromium", "chromium-browser", "cromite",
    "brave", "brave browser", "brave-browser", "brave browser beta",
    "brave browser nightly", "vivaldi", "vivaldi-stable", "opera", "opera gx",
    "microsoft edge", "microsoft-edge", "microsoft edge beta", "microsoft edge dev",
    "microsoft edge canary", "msedge", "thorium", "arc", "whale", "naver whale",
    "yandex", "yandex browser", "helium", "comet", "dia", "sidekick",
    # Firefox family
    "firefox", "firefox-esr", "mozilla firefox", "firefox developer edition",
    "firefox nightly", "librewolf", "zen", "zen browser", "zen-browser",
    "waterfox", "floorp", "mullvad browser", "tor browser", "palemoon",
    "pale moon", "seamonkey", "icecat", "basilisk",
    # WebKit and others
    "epiphany", "web", "gnome web", "org.gnome.epiphany", "falkon", "midori",
    "konqueror", "qutebrowser", "nyxt", "surf", "luakit", "angelfish",
    "ladybird", "duckduckgo", "safari", "safari technology preview", "orion",
})

# Desktop apps where Enter sends, beyond the chat list in
# voice_keyboard/flow/registers.py (CHAT_APPS): AI assistants, other
# messengers and the multi-messenger wrappers. A line break there is
# Shift+Enter, which is never worse than Enter in an app where Enter
# sends. Same spelling as BROWSER_APPS.
DESKTOP_CHAT_APPS = frozenset({
    # AI assistants: Enter sends the prompt.
    "chatgpt", "claude", "perplexity", "copilot", "microsoft copilot",
    "lm studio", "lmstudio", "poe", "msty",
    # Messengers
    "messenger", "caprine", "messages", "microsoft teams",
    "microsoft teams (work or school)", "microsoft teams classic", "msteams",
    "teams-for-linux", "zoom", "zoom.us", "zoom workplace", "webex",
    "ciscocollabhost", "viber", "line", "wechat", "weixin", "qq", "kakaotalk",
    "dingtalk", "feishu", "lark", "pidgin", "gajim", "dino", "nheko", "fractal",
    "schildichat", "fluffychat", "cinny", "keybase", "guilded", "revolt",
    "discordcanary", "discordptb", "discord canary", "discord ptb", "vesktop",
    "webcord", "armcord", "legcord", "equibop", "telegramdesktop",
    "telegram desktop", "64gram", "kotatogram", "ayugram", "materialgram",
    "unigram", "whatsapp-for-linux", "wasistlos", "zapzap", "whatsie",
    "signal beta", "whatsapp.root", "phone link", "phoneexperiencehost",
    # Several messengers in one window
    "ferdium", "franz", "rambox", "wavebox",
})

# Spreadsheets, where Enter (and Shift+Enter) commit the cell and move
# the selection: the rest of the dictation would then replace the next
# cell. A line break there presses nothing. Same spelling as BROWSER_APPS.
SPREADSHEET_APPS = frozenset({
    "excel", "microsoft excel", "gnumeric", "calligrasheets", "numbers",
})
# One app name for every part of an office suite: the window title says
# which part ("Budget - LibreOffice Calc").
_OFFICE_SUITE_APPS = frozenset({"soffice", "soffice.bin", "libreoffice", "localc"})
_SPREADSHEET_TITLES = (r"libreoffice calc",)

# Zero-width and bidi marks: Edge puts one inside "Microsoft Edge", and
# Windows titles often start with a left-to-right mark.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")
# A Store (UWP) app's window belongs to this host process on Windows; its
# title is the app's name ("WhatsApp").
_UWP_HOSTS = frozenset({"applicationframehost"})


def _app_keys(app: str) -> tuple[str, ...]:
    """The app name as config and the built-in lists spell it: lowercase,
    and for a Windows exe both with and without ".exe"."""
    key = _INVISIBLE.sub("", app or "").strip().lower()
    if key.endswith(".exe"):
        return (key, key[:-4])
    return (key,) if key else ()


def is_browser(app: str) -> bool:
    return any(key in BROWSER_APPS for key in _app_keys(app))


def is_desktop_chat_app(app: str) -> bool:
    """Enter sends here: the built-in chat list, and DESKTOP_CHAT_APPS."""
    return is_chat_app(app) or any(key in DESKTOP_CHAT_APPS for key in _app_keys(app))


def app_names(focus) -> list[str]:
    """The names the focused app goes by: its own and, for a Windows Store
    app (whose window belongs to ApplicationFrameHost.exe), the parts of
    its window title, which name the app ("WhatsApp", "Unigram")."""
    app = (getattr(focus, "app", "") or "").strip()
    names = [app] if app else []
    if any(key in _UWP_HOSTS for key in _app_keys(app)):
        names += title_segments(getattr(focus, "title", "") or "")
    return names


def is_spreadsheet_app(app: str, title: str = "") -> bool:
    keys = _app_keys(app)
    if any(key in SPREADSHEET_APPS for key in keys):
        return True
    return any(key in _OFFICE_SUITE_APPS for key in keys) and any(
        re.fullmatch(pattern, segment) for segment in title_segments(title) for pattern in _SPREADSHEET_TITLES
    )


@dataclass(frozen=True)
class Site:
    """A web app, recognised by its address or by its tab title."""

    name: str
    # The host or a parent domain: "slack.com" covers "app.slack.com".
    hosts: tuple[str, ...] = ()
    # Regexes one of which must match the start of the path; none = any path.
    paths: tuple[str, ...] = ()
    # Regexes one of which must match a whole title segment, case-blind.
    titles: tuple[str, ...] = ()


# Where Enter sends. In a browser these get Shift+Enter, as any unknown
# page does; knowing them matters because a chat match always beats a
# document match: the safe choice wins a tie.
CHAT_SITES = (
    Site("WhatsApp", ("web.whatsapp.com",), titles=(r"whatsapp(?: business)?(?: web)?",)),
    Site("Slack", ("slack.com",), titles=(r"slack",)),
    Site("Discord", ("discord.com", "discordapp.com"), titles=(r"discord",)),
    Site("Microsoft Teams", ("teams.microsoft.com", "teams.live.com", "teams.cloud.microsoft"),
         titles=(r"microsoft teams(?: \(.+\))?",)),
    Site("Messenger", ("messenger.com", "facebook.com"), titles=(r"messenger", r"facebook")),
    Site("Telegram", ("web.telegram.org",), titles=(r"telegram(?: web)?",)),
    Site("Google Chat", ("chat.google.com",), titles=(r"google chat",)),
    Site("Google Chat", ("mail.google.com",), paths=(r"/chat(?:/|$)",)),
    Site("Google Messages", ("messages.google.com",),
         titles=(r"google messages(?: for web)?", r"messages for web")),
    Site("Element", ("element.io",), titles=(r"element",)),
    Site("Mattermost", ("mattermost.com",), titles=(r"(?:.+ )?mattermost",)),
    Site("Zulip", ("zulipchat.com", "chat.zulip.org"), titles=(r"zulip",)),
    Site("Rocket.Chat", ("rocket.chat",), titles=(r"rocket\.chat",)),
    Site("ChatGPT", ("chatgpt.com", "chat.openai.com"), titles=(r"chatgpt",)),
    Site("Claude", ("claude.ai",), titles=(r"claude",)),
    Site("Gemini", ("gemini.google.com",), titles=(r"(?:google )?gemini",)),
    Site("Copilot", ("copilot.microsoft.com", "copilot.cloud.microsoft"),
         titles=(r"(?:microsoft )?copilot(?:: .*)?",)),
    Site("Copilot", ("github.com",), paths=(r"/copilot(?:/|$)",)),
    Site("Perplexity", ("perplexity.ai",), titles=(r"perplexity(?: ai)?",)),
    Site("LinkedIn", ("linkedin.com",), titles=(r"linkedin",)),
    Site("X", ("x.com", "twitter.com"), titles=(r"x", r"twitter")),
    Site("Instagram", ("instagram.com",), titles=(r"instagram",)),
    Site("Skype", ("web.skype.com",), titles=(r"skype",)),
)

# Document editors, where Enter makes a paragraph or the next list item.
# A title names one only after the document's own name ("Plan - Google
# Docs"), never as the whole title: a chat that happens to be called
# "Notion" stays a chat.
DOCUMENT_SITES = (
    Site("Google Docs", ("docs.google.com",), paths=(r"/(?:a/[^/]+/)?document(?:/|$)",),
         titles=(r"google docs",)),
    Site("Google Slides", ("docs.google.com",), paths=(r"/(?:a/[^/]+/)?presentation(?:/|$)",),
         titles=(r"google slides",)),
    Site("Notion", ("notion.so", "notion.site"), titles=(r"notion",)),
    Site("Microsoft Word", ("word-edit.officeapps.live.com", "word.cloud.microsoft"),
         titles=(r"(?:microsoft )?word(?: online| for the web)?",)),
    Site("Confluence", ("atlassian.net",), paths=(r"/wiki(?:/|$)",), titles=(r"confluence",)),
    Site("Dropbox Paper", ("paper.dropbox.com",), titles=(r"dropbox paper",)),
    Site("Coda", ("coda.io",), titles=(r"coda",)),
    Site("Quip", ("quip.com", "quip-amazon.com"), titles=(r"quip",)),
)

# Spreadsheets on the web: Enter and Shift+Enter commit the cell and move
# the selection, and the next words typed replace the next cell. A line
# break there presses nothing. As with documents, a title names one only
# after the sheet's own name ("Budget - Google Sheets").
SHEET_SITES = (
    Site("Google Sheets", ("docs.google.com",), paths=(r"/(?:a/[^/]+/)?spreadsheets(?:/|$)",),
         titles=(r"google sheets",)),
    Site("Microsoft Excel", ("excel.officeapps.live.com", "excel.cloud.microsoft"),
         titles=(r"(?:microsoft )?excel(?: online| for the web)?",)),
    Site("Zoho Sheet", ("sheet.zoho.com", "sheet.zoho.eu", "sheet.zoho.in"), titles=(r"zoho sheet",)),
    Site("Smartsheet", ("app.smartsheet.com", "app.smartsheet.eu"), titles=(r"smartsheet",)),
)

# Title separators need spaces around them: "Rocket.Chat", "e-mail" and
# "and/or" are one segment each.
_TITLE_SEPARATOR = re.compile(r"\s+[-‐-―|·•/]\s+")
_UNREAD_BEFORE = re.compile(r"^(?:\(\d+\+?\)|\[\d+\+?\]|[*•●]+)\s*")
_UNREAD_AFTER = re.compile(r"\s*(?:\(\d+\+?\)|\[\d+\+?\])$")
_MORE_PAGES = re.compile(r"\s+and \d+ more pages?$")  # Edge: "Chat and 3 more pages"


def title_segments(title: str) -> list[str]:
    """A window title split at its separators, each part casefolded and
    stripped of unread counters: "(3) Chat | Slack - Google Chrome" ->
    ["chat", "slack", "google chrome"]."""
    text = _INVISIBLE.sub("", title or "").casefold()
    segments = []
    for part in _TITLE_SEPARATOR.split(text):
        part = _MORE_PAGES.sub("", " ".join(part.split()))
        part = _UNREAD_AFTER.sub("", _UNREAD_BEFORE.sub("", part)).strip()
        if part:
            segments.append(part)
    return segments


def _host_and_path(address: str) -> tuple[str, str]:
    """("app.slack.com", "/client/T1") for a web address; ("", "") for
    anything that isn't http(s)."""
    address = (address or "").strip()
    if not address:
        return "", ""
    if "://" not in address:
        address = "https://" + address
    try:
        parts = urlsplit(address)
        host = (parts.hostname or "").rstrip(".").lower()
    except ValueError:
        return "", ""
    if parts.scheme.lower() not in ("http", "https") or not host:
        return "", ""
    return host, parts.path or "/"


def _host_matches(host: str, domain: str) -> bool:
    domain = domain.strip(".").lower()
    return bool(domain) and (host == domain or host.endswith("." + domain))


def _site_by_address(sites: Iterable[Site], addresses: Iterable[tuple[str, str]]) -> Optional[Site]:
    for host, path in addresses:
        for site in sites:
            if any(_host_matches(host, domain) for domain in site.hosts) and (
                not site.paths or any(re.match(p, path, re.IGNORECASE) for p in site.paths)
            ):
                return site
    return None


def _site_by_title(sites: Iterable[Site], segments: Iterable[str]) -> Optional[Site]:
    for segment in segments:
        for site in sites:
            if any(re.fullmatch(pattern, segment, re.IGNORECASE) for pattern in site.titles):
                return site
    return None


@dataclass(frozen=True)
class SiteMatch:
    kind: str  # "sheet", "chat" or "document"
    name: str


def identify_site(title: str = "", url: str = "", page_url: str = "") -> Optional[SiteMatch]:
    """Which known web app a browser window shows. `url` is the web
    document holding the caret, `page_url` the tab's top document (they
    differ when the caret is in a frame); either may be unknown.

    An address beats the title, and the safer choice wins a tie: a sheet
    (nothing) beats a chat (Shift+Enter), which beats a document (Enter).
    A Google Doc called "Slack" is a document when its address is known
    and a chat when only its title is."""
    addresses = [a for a in (_host_and_path(url), _host_and_path(page_url)) if a[0]]
    # The caret's own document only: a sheet or a document framed in
    # another page is not that page, and a chat widget framed inside a
    # document page is not that document.
    sheet = _site_by_address(SHEET_SITES, addresses[:1])
    if sheet is not None:
        return SiteMatch("sheet", sheet.name)
    chat = _site_by_address(CHAT_SITES, addresses)
    if chat is not None:
        return SiteMatch("chat", chat.name)
    document = _site_by_address(DOCUMENT_SITES, addresses[:1])
    if document is not None:
        return SiteMatch("document", document.name)
    segments = title_segments(title)
    sheet = _site_by_title(SHEET_SITES, segments[1:])
    if sheet is not None:
        return SiteMatch("sheet", sheet.name)
    chat = _site_by_title(CHAT_SITES, segments)
    if chat is not None:
        return SiteMatch("chat", chat.name)
    # The title describes the top page; it can name the document only
    # when the caret is in that page, not in a frame inside it.
    caret_in_page = not url or not page_url or url == page_url
    if caret_in_page:
        document = _site_by_title(DOCUMENT_SITES, segments[1:])
        if document is not None:
            return SiteMatch("document", document.name)
    return None


def chat_site(title: str = "", url: str = "", page_url: str = "") -> Optional[SiteMatch]:
    """The known chat this browser window shows, if it reads as one at
    all — by either address, or by any part of its title — whatever else
    it might also be."""
    addresses = [a for a in (_host_and_path(url), _host_and_path(page_url)) if a[0]]
    chat = _site_by_address(CHAT_SITES, addresses) or _site_by_title(CHAT_SITES, title_segments(title))
    return SiteMatch("chat", chat.name) if chat is not None else None


@dataclass(frozen=True)
class NewlineChoice:
    """What a line break presses in a session, and why."""

    key: str
    # terminal, unknown, single-line, override, chat-app, spreadsheet,
    # chat-site, document, browser or app
    reason: str
    # The matched site ("Slack") or config key; never a title or address.
    detail: str = ""

    def describe(self) -> str:
        why = f"{self.reason}: {self.detail}" if self.detail else self.reason
        return f"{self.key} ({why})"


# How strict each key is: a session may move to a stricter one when focus
# moves inside the same app, never back.
STRICTNESS = {ENTER: 0, SHIFT_ENTER: 1, NONE: 2}


def newline_table(table) -> list[tuple[str, object]]:
    """[registers.newline] as (name, value) pairs. A site written without
    quotes (web.whatsapp.com = "none") is a dotted key in TOML, which
    reads as nested tables; it is put back together here."""
    pairs: list[tuple[str, object]] = []

    def walk(node, prefix: str) -> None:
        for name, value in node.items():
            key = f"{prefix}.{name}" if prefix else str(name)
            if isinstance(value, Mapping):
                walk(value, key)
            else:
                pairs.append((key, value))

    if isinstance(table, Mapping):
        walk(table, "")
    return pairs


def newline_overrides(registers_cfg) -> dict[str, str]:
    """The user's rules from [registers]: chat_apps entries are
    "shift+enter", and a [registers.newline] entry wins over them."""
    cfg = registers_cfg if isinstance(registers_cfg, Mapping) else {}
    rules: dict[str, str] = {}
    chat_apps = cfg.get("chat_apps", []) or []
    if isinstance(chat_apps, (list, tuple)):
        for name in chat_apps:
            key = str(name).strip().lower()
            if key:
                rules[key] = SHIFT_ENTER
    for name, value in newline_table(cfg.get("newline", {}) or {}):
        key, choice = str(name).strip().lower(), normalize_key(value)
        if key and choice is not None:
            rules[key] = choice
    return rules


def _rule_by_address(rules: Mapping[str, str], host: str, path: str) -> Optional[str]:
    """The most specific rule naming this host ("google.com",
    "mail.google.com", "docs.google.com/spreadsheets"; a scheme in front
    and a port are ignored, and a path matches whole parts only)."""
    best, best_len = None, -1
    path = path.lower()
    for key in rules:
        site = re.sub(r"^[a-z][a-z0-9+.-]*://", "", key)
        if " " in site or "." not in site.split("/", 1)[0]:
            continue
        domain, _, prefix = site.partition("/")
        if not _host_matches(host, domain.split(":", 1)[0]):
            continue
        prefix = "/" + prefix.strip("/").lower()
        if prefix != "/" and path != prefix and not path.startswith(prefix + "/"):
            continue
        if len(site) > best_len:
            best, best_len = key, len(site)
    return best


def _names_site(match: SiteMatch, key: str) -> bool:
    """Does a rule's key name this built-in site itself ("slack" for
    Slack), rather than some other part of the page's title?"""
    key = key.casefold().strip()
    for site in CHAT_SITES + SHEET_SITES + DOCUMENT_SITES:
        if site.name == match.name and (
            key == site.name.casefold()
            or any(re.fullmatch(pattern, key, re.IGNORECASE) for pattern in site.titles)
        ):
            return True
    return False


def _matching_rule(focus, rules: Mapping[str, str], browser: bool) -> Optional[str]:
    if not rules:
        return None
    if browser:
        title = getattr(focus, "title", "") or ""
        for address in (getattr(focus, "url", ""), getattr(focus, "page_url", "")):
            host, path = _host_and_path(address)
            if host and (key := _rule_by_address(rules, host, path)) is not None:
                return key
        by_title = {key.casefold(): key for key in rules}
        chat = None
        for segment in title_segments(title):
            key = by_title.get(segment)
            if key is None:
                continue
            if rules[key] == ENTER and chat is None:
                chat = chat_site(title, getattr(focus, "url", "") or "", getattr(focus, "page_url", "") or "")
            if rules[key] == ENTER and chat is not None and not _names_site(chat, key):
                # Some other part of a chat's title ("Notion", the Slack
                # workspace; "Jira", a Teams channel): the rule was not
                # written for this chat, and Enter would send.
                continue
            return key
    for name in app_names(focus):
        for key in _app_keys(name):
            if key in rules:
                return key
    return None


def choose_newline(
    focus,
    *,
    register_terminal: bool = False,
    overrides: Optional[Mapping[str, str]] = None,
) -> NewlineChoice:
    """What a dictated line break presses in `focus` (a FocusInfo, or None
    when the probe saw nothing). `register_terminal`: the session's
    register treats the field as a terminal (a terminal app, or an app
    [registers.map] sends to a terminal register)."""
    if register_terminal:
        return NewlineChoice(NONE, "terminal")
    app = (getattr(focus, "app", "") or "").strip() if focus is not None else ""
    if not app:
        return NewlineChoice(NONE, "unknown")
    if register_for_app(app, getattr(focus, "role", "")) is TERMINAL:
        # A terminal mapped to another register is still a terminal.
        return NewlineChoice(NONE, "terminal")
    if getattr(focus, "single_line", False):
        return NewlineChoice(NONE, "single-line")
    browser = is_browser(app)
    title = getattr(focus, "title", "") or ""
    site = (
        identify_site(title, getattr(focus, "url", "") or "", getattr(focus, "page_url", "") or "")
        if browser
        else None
    )
    rules = overrides or {}
    rule = _matching_rule(focus, rules, browser)
    if rule is not None:
        return NewlineChoice(rules[rule], "override", rule)
    names = app_names(focus)
    chat = next((name for name in names if is_desktop_chat_app(name)), None)
    if chat is not None:
        return NewlineChoice(SHIFT_ENTER, "chat-app", chat)
    if not browser:
        sheet = next((name for name in names if is_spreadsheet_app(name, title)), None)
        if sheet is not None:
            return NewlineChoice(NONE, "spreadsheet", sheet)
        return NewlineChoice(ENTER, "app")
    if site is not None and site.kind == "sheet":
        return NewlineChoice(NONE, "spreadsheet", site.name)
    if site is not None and site.kind == "chat":
        return NewlineChoice(SHIFT_ENTER, "chat-site", site.name)
    if site is not None:
        return NewlineChoice(ENTER, "document", site.name)
    return NewlineChoice(SHIFT_ENTER, "browser")
