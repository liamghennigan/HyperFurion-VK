"""What a dictated line break presses: nothing in a terminal, unknown
focus or a one-line field; Shift+Enter in a chat or an unknown web page;
Enter in a document editor or any other app; and the user's own rules
in between. Pure policy, driven with fake FocusInfo values."""

import re

import pytest

from voice_keyboard.focusprobe import FocusInfo
from voice_keyboard.newline import (
    BROWSER_APPS,
    CHAT_SITES,
    DOCUMENT_SITES,
    ENTER,
    NONE,
    SHIFT_ENTER,
    NewlineChoice,
    choose_newline,
    identify_site,
    is_browser,
    newline_overrides,
    normalize_key,
    title_segments,
)


def choose(app="", *, overrides=None, register_terminal=False, **fields) -> NewlineChoice:
    return choose_newline(
        FocusInfo(app=app, **fields), register_terminal=register_terminal, overrides=overrides
    )


# ── 1. terminals and focus that can't be identified: nothing ──────────────

class TestNeverInATerminal:
    def test_a_terminal_register(self) -> None:
        assert choose("gedit", register_terminal=True) == NewlineChoice(NONE, "terminal")

    @pytest.mark.parametrize("focus", [None, FocusInfo(), FocusInfo(app="   ", role="text")])
    def test_unidentified_focus(self, focus) -> None:
        assert choose_newline(focus) == NewlineChoice(NONE, "unknown")

    @pytest.mark.parametrize("app, role", [("kitty", "text"), ("WindowsTerminal.exe", ""), ("someapp", "terminal")])
    def test_a_terminal_app_or_widget_whatever_its_register(self, app, role) -> None:
        # A terminal mapped to another register in [registers.map] is still one.
        assert choose(app, role=role).key == NONE
        assert choose(app, role=role).reason == "terminal"

    def test_no_rule_can_make_a_terminal_press_enter(self) -> None:
        assert choose("kitty", overrides={"kitty": ENTER}).key == NONE
        assert choose("gedit", register_terminal=True, overrides={"gedit": ENTER}).key == NONE


# ── 2. one-line fields: nothing ───────────────────────────────────────────

class TestOneLineFields:
    @pytest.mark.parametrize("app", ["gedit", "slack", "firefox", "chrome.exe"])
    def test_a_one_line_field_never_gets_a_line_break(self, app) -> None:
        assert choose(app, single_line=True, title="WhatsApp - Google Chrome") == NewlineChoice(NONE, "single-line")

    def test_beats_the_users_rules(self) -> None:
        # A rule says what a line break is in the app's text areas; a
        # search box or form field in it would still be submitted.
        assert choose("firefox", single_line=True, overrides={"firefox": ENTER}).key == NONE


# ── 3. the user's rules ───────────────────────────────────────────────────

class TestUserRules:
    def test_an_app_rule(self) -> None:
        assert choose("obsidian", overrides={"obsidian": SHIFT_ENTER}) == NewlineChoice(SHIFT_ENTER, "override", "obsidian")
        assert choose("Notepad.exe", overrides={"notepad": NONE}).key == NONE
        assert choose("Notepad.exe", overrides={"notepad.exe": SHIFT_ENTER}).key == SHIFT_ENTER

    def test_a_rule_beats_the_built_in_chat_list(self) -> None:
        assert choose("slack", overrides={"slack": ENTER}).key == ENTER

    def test_a_site_rule_matches_the_host_and_its_subdomains(self) -> None:
        rules = {"notion.so": SHIFT_ENTER}
        assert choose("firefox", url="https://www.notion.so/Plan-123", overrides=rules) == NewlineChoice(
            SHIFT_ENTER, "override", "notion.so"
        )
        assert choose("firefox", url="https://notnotion.so/", overrides=rules).reason != "override"

    def test_a_site_rule_with_a_path(self) -> None:
        rules = {"docs.google.com/spreadsheets": NONE}
        assert choose("chrome", url="https://docs.google.com/spreadsheets/d/1/edit", overrides=rules).key == NONE
        assert choose("chrome", url="https://docs.google.com/document/d/1/edit", overrides=rules).key == ENTER

    def test_the_most_specific_site_rule_wins(self) -> None:
        rules = {"google.com": ENTER, "mail.google.com": SHIFT_ENTER}
        assert choose("chrome", url="https://mail.google.com/mail/u/0/", overrides=rules).key == SHIFT_ENTER
        assert choose("chrome", url="https://www.google.com/search", overrides=rules).key == ENTER

    def test_a_title_rule_matches_a_whole_segment(self) -> None:
        rules = {"jira": ENTER}
        assert choose("firefox", title="PROJ-1 Fix it - Jira — Mozilla Firefox", overrides=rules).key == ENTER
        # Never a word inside a segment: "Jira notes" is someone's page.
        assert choose("firefox", title="Jira notes - Google Chrome", overrides=rules).key == SHIFT_ENTER

    def test_a_site_beats_a_title_which_beats_an_app(self) -> None:
        rules = {"firefox": ENTER, "whatsapp": NONE, "web.whatsapp.com": SHIFT_ENTER}
        assert choose("firefox", title="WhatsApp — Mozilla Firefox", url="https://web.whatsapp.com/",
                      overrides=rules).detail == "web.whatsapp.com"
        assert choose("firefox", title="WhatsApp — Mozilla Firefox", overrides=rules).detail == "whatsapp"
        assert choose("firefox", title="Inbox - Gmail — Mozilla Firefox", overrides=rules).detail == "firefox"

    def test_an_app_wide_rule_covers_every_page_in_that_browser(self) -> None:
        # The old behaviour, for whoever wants it back.
        assert choose("firefox", title="WhatsApp — Mozilla Firefox", overrides={"firefox": ENTER}).key == ENTER

    def test_titles_and_addresses_count_only_in_a_browser(self) -> None:
        rules = {"notes": SHIFT_ENTER, "example.com": NONE}
        assert choose("gedit", title="notes - gedit", url="https://example.com/", overrides=rules).key == ENTER


# ── 4. desktop chat apps: Shift+Enter ─────────────────────────────────────

@pytest.mark.parametrize("app", ["slack", "Slack", "Discord.exe", "signal-desktop", "ms-teams", "element-desktop"])
def test_a_desktop_chat_app_gets_shift_enter(app) -> None:
    choice = choose(app, role="text")
    assert (choice.key, choice.reason) == (SHIFT_ENTER, "chat-app")


def test_chat_apps_config_is_shorthand_for_shift_enter() -> None:
    rules = newline_overrides({"chat_apps": ["MyChat"]})
    assert rules == {"mychat": SHIFT_ENTER}
    assert choose("mychat", overrides=rules).key == SHIFT_ENTER
    # ...and now reaches a chat in a browser tab, by its title.
    assert choose("chrome", title="General - MyChat - Google Chrome", overrides=rules).key == SHIFT_ENTER


def test_a_newline_rule_wins_over_chat_apps() -> None:
    rules = newline_overrides({"chat_apps": ["mychat"], "newline": {"MyChat": "Enter"}})
    assert rules == {"mychat": ENTER}


# ── 5/6. browsers by site; other apps Enter ───────────────────────────────

@pytest.mark.parametrize("app", ["gedit", "soffice", "WINWORD.EXE", "notepad.exe", "TextEdit", "obsidian"])
def test_any_other_identified_app_gets_enter(app) -> None:
    assert choose(app, role="text") == NewlineChoice(ENTER, "app")


CHAT_URLS = [
    ("https://web.whatsapp.com/", "WhatsApp"),
    ("https://app.slack.com/client/T01/C02", "Slack"),
    ("https://myteam.slack.com/messages/general", "Slack"),
    ("https://discord.com/channels/1/2", "Discord"),
    ("https://teams.microsoft.com/v2/", "Microsoft Teams"),
    ("https://teams.live.com/v2/", "Microsoft Teams"),
    ("https://www.messenger.com/t/123", "Messenger"),
    ("https://www.facebook.com/messages/t/1", "Messenger"),
    ("https://web.telegram.org/a/", "Telegram"),
    ("https://chat.google.com/room/AAA", "Google Chat"),
    ("https://mail.google.com/chat/u/0/", "Google Chat"),
    ("https://messages.google.com/web/conversations", "Google Messages"),
    ("https://app.element.io/", "Element"),
    ("https://community.mattermost.com/core/channels/town-square", "Mattermost"),
    ("https://acme.zulipchat.com/", "Zulip"),
    ("https://open.rocket.chat/channel/general", "Rocket.Chat"),
    ("https://chatgpt.com/c/abc", "ChatGPT"),
    ("https://chat.openai.com/", "ChatGPT"),
    ("https://claude.ai/new", "Claude"),
    ("https://gemini.google.com/app", "Gemini"),
    ("https://copilot.microsoft.com/", "Copilot"),
    ("https://github.com/copilot", "Copilot"),
    ("https://www.perplexity.ai/", "Perplexity"),
    ("https://www.linkedin.com/messaging/thread/1/", "LinkedIn"),
    ("https://x.com/messages", "X"),
    ("https://twitter.com/messages", "X"),
    ("https://www.instagram.com/direct/inbox/", "Instagram"),
]


@pytest.mark.parametrize("url, name", CHAT_URLS)
def test_a_chat_site_by_address(url, name) -> None:
    assert choose("firefox", url=url, page_url=url) == NewlineChoice(SHIFT_ENTER, "chat-site", name)


CHAT_TITLES = [
    ("WhatsApp — Mozilla Firefox", "WhatsApp"),
    ("(3) WhatsApp - Google Chrome", "WhatsApp"),
    ("WhatsApp and 4 more pages - Personal - Microsoft​ Edge", "WhatsApp"),
    ("general (Channel) - Acme - Slack - Google Chrome", "Slack"),
    ("Slack | general | Acme", "Slack"),
    ("* random (Channel) - Acme - Slack — Mozilla Firefox", "Slack"),
    ("#general | Server - Discord - Google Chrome", "Discord"),
    ("Chat | Ada Lovelace | Microsoft Teams - Google Chrome", "Microsoft Teams"),
    ("Messenger | Facebook — Mozilla Firefox", "Messenger"),
    ("Telegram Web - Google Chrome", "Telegram"),
    ("Google Chat - Google Chrome", "Google Chat"),
    ("Google Messages for web - Google Chrome", "Google Messages"),
    ("Element [2] | Project room", "Element"),
    ("Town Square - Acme Mattermost - Google Chrome", "Mattermost"),
    ("general > deploys - Acme - Zulip", "Zulip"),
    ("Rocket.Chat - Google Chrome", "Rocket.Chat"),
    ("ChatGPT - Google Chrome", "ChatGPT"),
    ("Trip ideas - Claude — Mozilla Firefox", "Claude"),
    ("Google Gemini - Google Chrome", "Gemini"),
    ("Microsoft Copilot: Your AI companion - Microsoft​ Edge", "Copilot"),
    ("What is TOML? - Perplexity", "Perplexity"),
    ("(2) Messaging | LinkedIn - Google Chrome", "LinkedIn"),
    ("(1) Messages / X - Google Chrome", "X"),
    ("Inbox • Direct • Instagram - Google Chrome", "Instagram"),
]


@pytest.mark.parametrize("title, name", CHAT_TITLES)
def test_a_chat_site_by_title(title, name) -> None:
    assert choose("chrome.exe", title=title) == NewlineChoice(SHIFT_ENTER, "chat-site", name)


DOCUMENT_URLS = [
    ("https://docs.google.com/document/d/1abc/edit", "Google Docs"),
    ("https://docs.google.com/document/u/1/d/1abc/edit", "Google Docs"),
    ("https://docs.google.com/a/acme.com/document/d/1abc/edit", "Google Docs"),
    ("https://docs.google.com/presentation/d/1abc/edit", "Google Slides"),
    ("https://www.notion.so/acme/Plan-0123", "Notion"),
    ("https://acme.notion.site/Plan", "Notion"),
    ("https://word-edit.officeapps.live.com/we/wordeditorframe.aspx", "Microsoft Word"),
    ("https://acme.atlassian.net/wiki/spaces/ENG/pages/1/Plan", "Confluence"),
    ("https://paper.dropbox.com/doc/Plan--abc", "Dropbox Paper"),
    ("https://coda.io/d/Plan_dabc", "Coda"),
    ("https://acme.quip.com/abc/Plan", "Quip"),
]


@pytest.mark.parametrize("url, name", DOCUMENT_URLS)
def test_a_document_editor_by_address_gets_enter(url, name) -> None:
    assert choose("chromium", url=url, page_url=url) == NewlineChoice(ENTER, "document", name)


DOCUMENT_TITLES = [
    ("Q3 plan - Google Docs - Google Chrome", "Google Docs"),
    ("Q3 plan - Google Docs — Mozilla Firefox", "Google Docs"),
    ("Kickoff - Google Slides - Google Chrome", "Google Slides"),
    ("Roadmap | Notion - Google Chrome", "Notion"),
    ("Report.docx - Microsoft Word Online - Google Chrome", "Microsoft Word"),
    ("Plan - Engineering - Confluence — Mozilla Firefox", "Confluence"),
    ("Plan - Dropbox Paper - Google Chrome", "Dropbox Paper"),
    ("Plan · Coda - Google Chrome", "Coda"),
    ("Plan - Quip - Google Chrome", "Quip"),
]


@pytest.mark.parametrize("title, name", DOCUMENT_TITLES)
def test_a_document_editor_by_title_gets_enter(title, name) -> None:
    assert choose("msedge.exe", title=title) == NewlineChoice(ENTER, "document", name)


@pytest.mark.parametrize("app, title, url", [
    ("firefox", "Inbox (3) - you@example.com - Gmail — Mozilla Firefox", "https://mail.google.com/mail/u/0/"),
    ("chrome", "Contact us - Example Corp - Google Chrome", "https://example.com/contact"),
    ("brave", "", ""),
])
def test_any_other_web_page_gets_shift_enter(app, title, url) -> None:
    assert choose(app, title=title, url=url, page_url=url) == NewlineChoice(SHIFT_ENTER, "browser")


# ── matching titles conservatively ────────────────────────────────────────

class TestConservativeMatching:
    def test_a_doc_titled_slack_notes_is_a_doc(self) -> None:
        assert choose("chrome", title="Slack notes - Google Docs - Google Chrome").key == ENTER

    def test_a_doc_titled_slack_is_a_chat_when_only_the_title_is_known(self) -> None:
        # Both could match: the safe choice wins.
        assert choose("chrome", title="Slack - Google Docs - Google Chrome").key == SHIFT_ENTER

    def test_the_address_beats_the_title(self) -> None:
        url = "https://docs.google.com/document/d/1/edit"
        assert choose("chrome", title="Slack - Google Docs - Google Chrome", url=url, page_url=url).key == ENTER

    def test_a_chat_address_beats_a_document_title(self) -> None:
        url = "https://chatgpt.com/c/1"
        assert choose("chrome", title="Plan - Google Docs - Google Chrome", url=url, page_url=url).key == SHIFT_ENTER

    def test_a_site_name_alone_is_not_a_document(self) -> None:
        # A chat conversation called "Notion" stays a web page.
        assert choose("chrome", title="Notion - Google Chrome").key == SHIFT_ENTER
        assert choose("chrome", title="Google Docs - Google Chrome").key == SHIFT_ENTER

    def test_a_chat_framed_in_a_document_page_is_not_the_document(self) -> None:
        inner, page = "https://widget.intercom.io/frame", "https://www.notion.so/Plan"
        assert choose("chrome", url=inner, page_url=page, title="Plan | Notion - Google Chrome").key == SHIFT_ENTER

    def test_a_document_in_a_frame_of_its_own_page(self) -> None:
        url = "https://word-edit.officeapps.live.com/we/wordeditorframe.aspx"
        page = "https://acme.sharepoint.com/sites/x/_layouts/15/Doc.aspx"
        assert choose("msedge", url=url, page_url=page).key == ENTER

    def test_a_page_with_an_unknown_address_may_still_be_named_by_its_title(self) -> None:
        url = "https://wiki.acme.example/display/ENG/Plan"
        assert choose("firefox", url=url, page_url=url, title="Plan - Engineering - Confluence — Mozilla Firefox").key == ENTER

    def test_lookalike_hosts_are_not_the_site(self) -> None:
        for url in ("https://slack.com.evil.example/", "https://notslack.com/", "https://docs.google.com.evil.example/document/"):
            assert identify_site(url=url, page_url=url) is None

    def test_non_web_addresses_are_ignored(self) -> None:
        assert identify_site(url="file:///home/me/slack.html") is None
        assert identify_site(url="about:blank", page_url="about:blank") is None


# ── the pieces ────────────────────────────────────────────────────────────

class TestBrowsers:
    @pytest.mark.parametrize("app", [
        "Google Chrome", "chromium", "Firefox", "firefox.exe", "chrome.exe", "msedge.exe",
        "Microsoft​ Edge", "Brave Browser", "brave.exe", "Vivaldi", "opera.exe",
        "LibreWolf", "Zen", "Web", "epiphany", "Safari", "Arc",
    ])
    def test_known_browsers(self, app) -> None:
        assert is_browser(app)

    @pytest.mark.parametrize("app", ["", "gedit", "slack", "code", "notepad.exe", "chromeless"])
    def test_not_browsers(self, app) -> None:
        assert not is_browser(app)

    def test_one_list_lowercase(self) -> None:
        assert all(name == name.lower().strip() and not name.endswith(".exe") for name in BROWSER_APPS)


class TestTitleSegments:
    def test_separators_and_browser_suffixes(self) -> None:
        assert title_segments("Chat | Slack - Google Chrome") == ["chat", "slack", "google chrome"]
        assert title_segments("WhatsApp — Mozilla Firefox") == ["whatsapp", "mozilla firefox"]
        assert title_segments("Home / X") == ["home", "x"]

    def test_unread_counters_and_edge_tab_counts(self) -> None:
        assert title_segments("(12) WhatsApp") == ["whatsapp"]
        assert title_segments("* general - Slack") == ["general", "slack"]
        assert title_segments("Element [3] | Room") == ["element", "room"]
        assert title_segments("Inbox and 2 more pages - Work - Microsoft​ Edge") == ["inbox", "work", "microsoft edge"]

    def test_unspaced_punctuation_is_not_a_separator(self) -> None:
        assert title_segments("Rocket.Chat") == ["rocket.chat"]
        assert title_segments("e-mail and/or chat") == ["e-mail and/or chat"]

    def test_empty(self) -> None:
        assert title_segments("") == [] and title_segments(" - ") == []


class TestConfigValues:
    @pytest.mark.parametrize("value, key", [
        ("enter", ENTER), ("Return", ENTER), ("shift+enter", SHIFT_ENTER), ("Shift + Enter", SHIFT_ENTER),
        ("shift-enter", SHIFT_ENTER), ("shift+return", SHIFT_ENTER), ("none", NONE), ("Nothing", NONE),
        ("off", NONE), ("ctrl+enter", None), ("", None), (True, None), (None, None),
    ])
    def test_normalize(self, value, key) -> None:
        assert normalize_key(value) == key

    def test_overrides_skip_what_validation_would_reject(self) -> None:
        assert newline_overrides({"newline": {"a": "bogus", " ": "enter", "B": "none"}}) == {"b": NONE}
        assert newline_overrides(None) == {} and newline_overrides({"newline": "nope", "chat_apps": "x"}) == {}

    def test_describe_names_the_site_not_the_title(self) -> None:
        choice = choose("chrome", title="Secret project - Claude - Google Chrome")
        assert choice.describe() == "shift+enter (chat-site: Claude)"
        assert "Secret" not in repr(choice)


class TestBuiltInTables:
    def test_every_site_can_be_recognised_and_its_patterns_compile(self) -> None:
        for site in CHAT_SITES + DOCUMENT_SITES:
            assert site.hosts or site.titles, site.name
            for pattern in site.paths + site.titles:
                re.compile(pattern)
            assert all(h == h.lower() and "/" not in h for h in site.hosts), site.name

    def test_no_site_title_is_a_browser_name(self) -> None:
        for site in CHAT_SITES + DOCUMENT_SITES:
            for pattern in site.titles:
                assert not any(re.fullmatch(pattern, name) for name in BROWSER_APPS), (site.name, pattern)

    def test_every_chat_title_still_reads_as_a_chat(self) -> None:
        # A document pattern that also fit a chat's title would press Enter there.
        for title, _ in CHAT_TITLES:
            assert identify_site(title=title).kind == "chat", title


def test_a_site_rule_may_be_written_with_a_scheme_or_any_case() -> None:
    rules = newline_overrides({"newline": {"https://Notion.so/": "shift+enter", "Straße": "none"}})
    assert choose("chrome", url="https://www.notion.so/Plan", overrides=rules).detail == "https://notion.so/"
    assert choose("chrome", title="Karte - STRASSE - Google Chrome", overrides=rules).key == NONE
