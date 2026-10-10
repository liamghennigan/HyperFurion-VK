"""The landing page's `ask` answers from DOCS_CONTEXT, so every fact in it
must be true of the released app. These pin the facts that were once wrong
(a $5/mo plan on sale, Linux only, the old raw install.sh, ASCII-only
typing, a Ctrl+Alt+T read-aloud key, an Esc that cancels)."""

from hyperfurion_relay.demo import DOCS_CONTEXT

RELEASE_INSTALLER = (
    "https://github.com/liamghennigan/HyperFurion-VK/releases/latest/download/"
    "install-hyperfurion-vk.sh"
)


def _flat() -> str:
    return " ".join(DOCS_CONTEXT.split())


def test_platforms():
    text = _flat()
    assert "Linux (Wayland and X11) and Windows 10/11" in text
    assert "macOS is in beta" in text
    assert "a Linux voice keyboard" not in text


def test_install_commands_are_the_released_ones():
    assert RELEASE_INSTALLER in DOCS_CONTEXT
    assert "HyperFurion-VK-Setup.run" in DOCS_CONTEXT
    assert "HyperFurion-VK-Setup.cmd" in DOCS_CONTEXT
    assert "main/install.sh" not in DOCS_CONTEXT


def test_subscription_is_not_offered():
    text = _flat()
    assert "hosted subscription isn't on sale" in text
    assert "$" not in text
    assert "/mo" not in text


def test_offline_and_enter_policy():
    text = _flat()
    assert "No speech model ships" in text
    assert "local language model" in text
    assert "terminal it recognizes it never presses Enter" in text
    assert "Shift+Enter in chat apps" in text


def test_kai_is_off_until_turned_on_with_an_online_service():
    # 2.4.0: Kai is on by default only when it runs locally; with xAI (the
    # default) "hold Right Ctrl" alone would be untrue.
    text = _flat()
    assert "on by default only with your own speech server" in text
    assert "voice-keyboard kai on" in text


def test_no_stale_facts():
    text = _flat()
    for stale in ("ASCII", "Ctrl+Alt+T", "Esc cancels", "hfk_"):
        assert stale not in text, stale


def test_short_enough_for_a_small_prompt():
    assert len(DOCS_CONTEXT) < 2400
