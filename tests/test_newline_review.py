"""Holes found reviewing the line-break policy, each pinned shut: desktop
AI and chat apps where Enter sends, spreadsheets where Enter commits a
cell, a user's title rule that happens to match part of a chat's title,
unquoted site keys in config, search combo boxes, typing outside a
session where focus can't be identified, and a browser tab switched
mid-dictation."""

import asyncio
from unittest import mock

import pytest
from fakes import FakeStreamingSTT, RecordingInjector, _make_daemon

from voice_keyboard.config import _default_config_with_paths, load_config, validate_config
from voice_keyboard.flow.registers import PROSE
from voice_keyboard.focusprobe import FocusInfo, atspi_single_line
from voice_keyboard.newline import (
    BROWSER_APPS,
    DESKTOP_CHAT_APPS,
    ENTER,
    NONE,
    SHIFT_ENTER,
    SHEET_SITES,
    SPREADSHEET_APPS,
    STRICTNESS,
    NewlineChoice,
    choose_newline,
    identify_site,
    is_browser,
    newline_overrides,
    newline_table,
)


def choose(app="", *, overrides=None, **fields) -> NewlineChoice:
    return choose_newline(FocusInfo(app=app, **fields), overrides=overrides)


# ── desktop apps where Enter sends ────────────────────────────────────────

@pytest.mark.parametrize("app", [
    "claude.exe", "Claude", "ChatGPT.exe", "ChatGPT", "Perplexity", "Messenger.exe",
    "Microsoft Teams", "teams-for-linux", "Zoom.exe", "zoom.us", "vesktop", "DiscordCanary.exe",
    "Ferdium", "WeChat.exe", "LINE.exe", "Messages",
])
def test_desktop_ai_and_chat_apps_get_shift_enter(app) -> None:
    # Enter sends the half-dictated prompt or message in all of them.
    assert choose(app, role="text") == NewlineChoice(SHIFT_ENTER, "chat-app", app)


def test_the_extra_chat_list_is_spelled_like_the_others() -> None:
    for name in DESKTOP_CHAT_APPS | SPREADSHEET_APPS:
        assert name == name.strip().lower() and not name.endswith(".exe"), name
    assert not (DESKTOP_CHAT_APPS & BROWSER_APPS) and not (SPREADSHEET_APPS & BROWSER_APPS)


def test_a_rule_still_beats_the_extra_chat_list() -> None:
    assert choose("claude.exe", overrides={"claude": ENTER}).key == ENTER


# ── spreadsheets: Enter commits the cell, then typing replaces the next ──

class TestSpreadsheets:
    @pytest.mark.parametrize("app, title", [
        ("EXCEL.EXE", "Budget.xlsx - Excel"),
        ("Microsoft Excel", ""),
        ("soffice", "Budget.ods - LibreOffice Calc"),
        ("soffice.bin", "Budget.ods - LibreOffice Calc"),
        ("gnumeric", "budget.gnumeric"),
    ])
    def test_native_spreadsheets_press_nothing(self, app, title) -> None:
        assert choose(app, role="table cell", title=title).key == NONE
        assert choose(app, role="table cell", title=title).reason == "spreadsheet"

    def test_the_rest_of_an_office_suite_keeps_enter(self) -> None:
        assert choose("soffice", role="paragraph", title="Letter.odt - LibreOffice Writer").key == ENTER
        assert choose("soffice", role="paragraph").key == ENTER

    @pytest.mark.parametrize("url, title, name", [
        ("https://docs.google.com/spreadsheets/d/1/edit", "", "Google Sheets"),
        ("https://docs.google.com/a/acme.com/spreadsheets/d/1/edit", "", "Google Sheets"),
        ("", "Budget - Google Sheets - Google Chrome", "Google Sheets"),
        ("https://excel.officeapps.live.com/x/_layouts/xlviewerinternal.aspx", "", "Microsoft Excel"),
        ("", "Budget.xlsx - Microsoft Excel Online - Microsoft​ Edge", "Microsoft Excel"),
        ("https://sheet.zoho.com/sheet/open/abc", "", "Zoho Sheet"),
    ])
    def test_web_spreadsheets_press_nothing(self, url, title, name) -> None:
        assert choose("chrome.exe", url=url, page_url=url, title=title) == NewlineChoice(NONE, "spreadsheet", name)

    def test_a_sheet_beats_a_chat_on_a_tie(self) -> None:
        # Titled "Slack": nothing is safer than Shift+Enter, which would
        # still commit the cell.
        assert choose("chrome", title="Slack - Google Sheets - Google Chrome").key == NONE
        # A sheet open in a Teams tab: the frame holding the caret decides.
        sheet, teams = "https://docs.google.com/spreadsheets/d/1/edit", "https://teams.microsoft.com/v2/"
        assert choose("chrome", url=sheet, page_url=teams).key == NONE

    def test_a_sheet_name_alone_is_not_a_sheet(self) -> None:
        assert choose("chrome", title="Google Sheets - Google Chrome").key == SHIFT_ENTER

    def test_a_document_is_not_a_sheet(self) -> None:
        url = "https://docs.google.com/document/d/1/edit"
        assert choose("chrome", url=url, page_url=url).key == ENTER

    def test_your_rule_can_bring_enter_back(self) -> None:
        # Moving down a row on "new line" is a choice you can make.
        assert choose("EXCEL.EXE", overrides={"excel": ENTER}).key == ENTER
        assert choose("chrome", url="https://docs.google.com/spreadsheets/d/1", overrides={
            "docs.google.com/spreadsheets": ENTER}).key == ENTER

    def test_identify_site_names_the_kind(self) -> None:
        assert identify_site(title="Plan - Google Sheets").kind == "sheet"
        assert all(site.hosts and site.titles for site in SHEET_SITES)


# ── a title rule written for something else must not send in a chat ──────

class TestIncidentalTitleRules:
    def test_an_app_rule_that_matches_part_of_a_chats_title(self) -> None:
        # notion = "enter" was written for Notion; this Slack workspace is
        # called Notion. Enter would send the half-dictated message.
        title = "general (Channel) - Notion - Slack - Google Chrome"
        choice = choose("chrome", title=title, overrides={"notion": ENTER})
        assert choice == NewlineChoice(SHIFT_ENTER, "chat-site", "Slack")

    def test_a_teams_channel_named_like_a_rule(self) -> None:
        title = "Jira | General | Microsoft Teams - Google Chrome"
        assert choose("chrome.exe", title=title, overrides={"jira": ENTER}).key == SHIFT_ENTER

    def test_a_chat_whose_title_also_names_a_sheet(self) -> None:
        # The page ranks as a sheet (nothing), but it still reads as a
        # chat: an "enter" rule for some other part of it must not apply.
        title = "general (Channel) - Excel - Acme - Slack - Google Chrome"
        assert choose("chrome", title=title).key == NONE
        assert choose("chrome", title=title, overrides={"excel": ENTER}).key == NONE

    def test_every_chat_title_with_an_incidental_enter_rule(self) -> None:
        from test_newline import CHAT_TITLES

        for title, _ in CHAT_TITLES:
            parts = title.split(" - ")
            for word in ("notion", "jira", "word", "excel", "google docs", "obsidian"):
                spliced = " - ".join(parts[:1] + [word] + parts[1:])
                assert choose("chrome.exe", title=spliced, overrides={word: ENTER}).key != ENTER, spliced

    def test_a_chat_known_by_its_address(self) -> None:
        url = "https://app.slack.com/client/T1/C2"
        title = "general (Channel) - Notion - Slack - Google Chrome"
        assert choose("chrome", url=url, page_url=url, title=title, overrides={"notion": ENTER}).key == SHIFT_ENTER

    def test_a_rule_that_names_the_chat_itself_still_wins(self) -> None:
        # You set Slack to send with Ctrl+Enter and want paragraphs there.
        assert choose("chrome", title="general (Channel) - Acme - Slack - Google Chrome",
                      overrides={"slack": ENTER}) == NewlineChoice(ENTER, "override", "slack")
        assert choose("firefox", title="WhatsApp — Mozilla Firefox", overrides={"WhatsApp": ENTER}).key == ENTER

    def test_safe_rules_still_apply_and_other_pages_are_untouched(self) -> None:
        title = "general (Channel) - Notion - Slack - Google Chrome"
        assert choose("chrome", title=title, overrides={"notion": NONE}).key == NONE
        assert choose("chrome", title="PROJ-1 Fix it - Jira - Google Chrome", overrides={"jira": ENTER}).key == ENTER

    def test_a_site_rule_or_a_browser_wide_rule_is_explicit(self) -> None:
        url = "https://app.slack.com/client/T1/C2"
        assert choose("chrome", url=url, overrides={"app.slack.com": ENTER}).key == ENTER
        assert choose("firefox", title="WhatsApp — Mozilla Firefox", overrides={"firefox": ENTER}).key == ENTER


# ── config: a site written without quotes ────────────────────────────────

class TestUnquotedSiteKeys:
    def test_dotted_keys_are_put_back_together(self) -> None:
        table = {"web": {"whatsapp": {"com": "none"}}, "notion": {"so": "shift+enter"}, "obsidian": "enter"}
        assert sorted(newline_table(table)) == [
            ("notion.so", "shift+enter"), ("obsidian", "enter"), ("web.whatsapp.com", "none"),
        ]
        assert newline_overrides({"newline": table}) == {
            "web.whatsapp.com": NONE, "notion.so": SHIFT_ENTER, "obsidian": ENTER,
        }

    def test_loaded_from_toml_and_valid(self, tmp_path) -> None:
        path = tmp_path / "config.toml"
        path.write_text('[registers.newline]\nweb.whatsapp.com = "none"\n', encoding="utf-8")
        cfg = load_config(path)
        cfg["xai"]["api_key"] = "k"
        validate_config(cfg)
        rules = newline_overrides(cfg["registers"])
        assert choose("firefox", url="https://web.whatsapp.com/", overrides=rules).key == NONE

    def test_a_bad_value_deep_inside_is_still_refused(self) -> None:
        cfg = _default_config_with_paths()
        cfg["xai"]["api_key"] = "k"
        cfg["registers"]["newline"] = {"web": {"whatsapp": {"com": "send"}}}
        with pytest.raises(RuntimeError, match="web.whatsapp.com"):
            validate_config(cfg)


class TestSiteRuleDetails:
    def test_a_port_in_the_rule_is_ignored(self) -> None:
        rules = {"intranet.example.com:8443/wiki": ENTER}
        assert choose("chrome", url="https://intranet.example.com/wiki/Plan", overrides=rules).key == ENTER

    def test_a_path_matches_whole_parts_only(self) -> None:
        rules = {"example.com/wiki": ENTER}
        assert choose("chrome", url="https://example.com/wiki", overrides=rules).key == ENTER
        assert choose("chrome", url="https://example.com/wikipedia/x", overrides=rules).key == SHIFT_ENTER


@pytest.mark.parametrize("app", [
    "konqueror", "Firefox Developer Edition", "Opera GX", "whale.exe", "palemoon.exe", "Yandex",
    "DuckDuckGo.exe", "Ladybird", "Cromite",
])
def test_more_browsers(app) -> None:
    assert is_browser(app)


def test_strictness_orders_the_keys() -> None:
    assert STRICTNESS[ENTER] < STRICTNESS[SHIFT_ENTER] < STRICTNESS[NONE]


# ── the probe: search combo boxes are one-line ────────────────────────────

@pytest.mark.parametrize("role, multi, tag", [
    ("combo box", True, "textarea"),  # Google's search box
    ("combo box", False, "input"),
    ("combo box", False, ""),
    ("autocomplete", False, ""),
])
def test_a_combo_box_is_one_line(role, multi, tag) -> None:
    assert atspi_single_line(role, single=False, multi=multi, tag=tag) is True


def test_google_search_in_a_browser_presses_nothing() -> None:
    from test_newline_probe import browser, field, page, parsed, run_probe

    box = field("combo box", states=("editable", "multi-line"), tag="textarea")
    payload = run_probe(browser("Google Chrome", "Google - Google Chrome", page("https://www.google.com/", box)))
    assert choose_newline(parsed(payload)) == NewlineChoice(NONE, "single-line")


@pytest.mark.parametrize("style, single", [(0, True), (0x0004, False)])
def test_a_winforms_rich_text_box_line_mode(windows_probe_fixture, style, single) -> None:
    info = windows_probe_fixture(class_name="WindowsForms10.RichEdit20W.app.0.141b42a_r6_ad1", style=style)
    assert info.single_line is single


@pytest.fixture
def windows_probe_fixture(monkeypatch: pytest.MonkeyPatch):
    from test_newline_probe import FakeKernel32, FakeUser32, _guithreadinfo_type

    from voice_keyboard import focusprobe

    def probe(**user32_kw) -> FocusInfo:
        monkeypatch.setattr(focusprobe, "_win_api", (FakeUser32(**user32_kw), FakeKernel32(), _guithreadinfo_type()))
        info = focusprobe._probe_windows_foreground()
        assert info is not None
        return info

    return probe


# ── the daemon ────────────────────────────────────────────────────────────

class NewlineInjector(RecordingInjector):
    """A newline is Enter, Shift+Enter while shift_newline is set, and a
    space while Enter is refused."""

    def __init__(self):
        super().__init__()
        self.suppress_enter = False
        self.shift_newline = False
        self.enters = 0

    def type_text(self, text: str) -> None:
        if self.suppress_enter:
            text = text.replace("\n", " ")
        elif not self.shift_newline:
            self.enters += text.count("\n")
        super().type_text(text)


@pytest.fixture(autouse=True)
def inline(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    async def _to_thread(func, /, *args, **kwargs):
        await asyncio.sleep(0)
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _to_thread)
    monkeypatch.setattr("voice_keyboard.client._show_overlay", lambda *a, **k: None)


class TestOutsideASessionUnknownFocus:
    def test_probe_switched_off_is_unknown_too(self) -> None:
        injector = NewlineInjector()
        daemon = _make_daemon(FakeStreamingSTT([]), injector)
        daemon._config["registers"]["probe"] = False
        with mock.patch("voice_keyboard.daemon.probe_focus") as probe:
            asyncio.run(daemon._type_text("make\nbuild"))
        probe.assert_not_called()
        assert injector.enters == 0 and injector.screen == "make build"
        assert injector.suppress_enter is False

    def test_a_transform_never_presses_enter_where_focus_is_unknown(self) -> None:
        # The model's rewrite may split lines; in a terminal the probe
        # can't see, Enter would run them.
        injector = NewlineInjector()
        daemon = _make_daemon(FakeStreamingSTT([]), injector)
        daemon._last_typed = "earlier"
        llm = mock.Mock()
        llm.rewrite = mock.Mock(return_value="ls\nrm -rf build")
        with mock.patch("voice_keyboard.daemon.probe_focus", return_value=None), \
                mock.patch("voice_keyboard.daemon.create_llm_client", return_value=llm):
            asyncio.run(daemon._transform_last("make it two lines"))
        assert injector.enters == 0 and "\n" not in injector.screen


GOOGLE_DOC = FocusInfo(app="Google Chrome", role="entry", title="Plan - Google Docs - Google Chrome",
                       url="https://docs.google.com/document/d/1/edit",
                       page_url="https://docs.google.com/document/d/1/edit")
WHATSAPP_TAB = FocusInfo(app="Google Chrome", role="entry", title="WhatsApp - Google Chrome",
                         url="https://web.whatsapp.com/", page_url="https://web.whatsapp.com/")
SEARCH_BOX = FocusInfo(app="Google Chrome", role="entry", single_line=True, title="WhatsApp - Google Chrome")


def session_in(focus, injector):
    daemon = _make_daemon(FakeStreamingSTT([]), injector)
    daemon._session_focus = focus
    daemon._session_register = PROSE
    daemon._flow_engine = object()  # a session is running
    daemon._session_newline = daemon._newline_choice(focus, PROSE)
    daemon._arm_newline(daemon._session_newline)
    return daemon


class TestMidSessionTabSwitch:
    def test_tightens_and_never_loosens(self) -> None:
        injector = NewlineInjector()
        daemon = session_in(GOOGLE_DOC, injector)
        assert daemon._session_newline.key == ENTER
        daemon._tighten_newline(WHATSAPP_TAB)
        assert daemon._session_newline == NewlineChoice(SHIFT_ENTER, "chat-site", "WhatsApp")
        assert (injector.suppress_enter, injector.shift_newline) == (False, True)
        daemon._tighten_newline(SEARCH_BOX)
        assert daemon._session_newline.key == NONE and (injector.suppress_enter, injector.shift_newline) == (True, False)
        daemon._tighten_newline(GOOGLE_DOC)  # back in the doc: stays strict
        assert daemon._session_newline.key == NONE and injector.suppress_enter is True
        assert daemon._newline_space_reason() == "a one-line field, where Enter would submit it"

    def test_nothing_happens_outside_a_session(self) -> None:
        injector = NewlineInjector()
        daemon = session_in(GOOGLE_DOC, injector)
        daemon._flow_engine = None
        daemon._tighten_newline(SEARCH_BOX)
        assert daemon._session_newline.key == ENTER and injector.suppress_enter is False

    def test_the_watchdog_does_it_for_the_same_app(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("voice_keyboard.daemon.FOCUS_WATCHDOG_S", 0)
        injector = NewlineInjector()
        daemon = session_in(GOOGLE_DOC, injector)
        daemon._recording = True
        probes = iter([WHATSAPP_TAB, None, GOOGLE_DOC])

        def probe():
            focus = next(probes, None)
            if focus is GOOGLE_DOC:
                daemon._recording = False
            return focus

        with mock.patch("voice_keyboard.daemon.probe_focus", side_effect=probe):
            asyncio.run(daemon._focus_watchdog_loop())
        assert daemon._session_newline.key == SHIFT_ENTER and injector.shift_newline is True
        assert daemon._focus_lost is False  # a tab switch is not an app switch

    def test_a_dictation_that_switches_tabs_never_sends(self, monkeypatch: pytest.MonkeyPatch) -> None:
        injector = NewlineInjector()
        daemon = session_in(GOOGLE_DOC, injector)
        daemon._tighten_newline(WHATSAPP_TAB)
        injector.type_text("hello\nthere")
        assert injector.enters == 0


# ── Windows Store apps: the window belongs to ApplicationFrameHost.exe ───

class TestStoreApps:
    @pytest.mark.parametrize("title", ["‎WhatsApp", "WhatsApp", "Unigram", "Phone Link"])
    def test_a_store_chat_app_is_named_by_its_window_title(self, title) -> None:
        choice = choose("ApplicationFrameHost.exe", title=title)
        assert (choice.key, choice.reason) == (SHIFT_ENTER, "chat-app")

    def test_other_store_apps_keep_enter(self) -> None:
        assert choose("ApplicationFrameHost.exe", title="Sticky Notes").key == ENTER
        assert choose("ApplicationFrameHost.exe").key == ENTER

    def test_rules_reach_a_store_app_by_its_name(self) -> None:
        assert choose("ApplicationFrameHost.exe", title="WhatsApp", overrides={"whatsapp": NONE}).key == NONE

    def test_only_the_store_host_is_named_by_its_title(self) -> None:
        # Any other app's title is a document's name, not the app's.
        assert choose("notepad.exe", title="whatsapp - Notepad").key == ENTER
