"""macOS permissions, the doctor's macOS checks, and recording feedback
without an overlay — all with the system calls replaced, so they run on
any platform."""

import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from voice_keyboard import client, doctor
from voice_keyboard.macos import overlay, permissions


class TestResponsibleApp:
    @pytest.mark.parametrize("env, expected", [
        ({"TERM_PROGRAM": "Apple_Terminal"}, "Terminal"),
        ({"TERM_PROGRAM": "iTerm.app"}, "iTerm"),
        ({"TERM_PROGRAM": "WezTerm"}, "WezTerm"),
        ({"TERM_PROGRAM": "ghostty"}, "Ghostty"),
        ({"TERM_PROGRAM": "WarpTerminal"}, "Warp"),
        ({"KITTY_WINDOW_ID": "1"}, "kitty"),
        ({"ALACRITTY_SOCKET": "/tmp/x"}, "Alacritty"),
    ])
    def test_terminals(self, env, expected) -> None:
        assert permissions.responsible_app(env, parent_pid=500) == expected

    def test_vs_code(self) -> None:
        assert "Visual Studio Code" in permissions.responsible_app({"TERM_PROGRAM": "vscode"}, parent_pid=9)

    def test_launchd_means_python_itself(self) -> None:
        who = permissions.responsible_app({}, parent_pid=1, executable=sys.executable)
        assert who.startswith("Python (") and who.endswith(")")

    def test_unknown(self) -> None:
        assert "started" in permissions.responsible_app({}, parent_pid=4321)


class TestChecks:
    def _checks(self, **overrides):
        values = dict(
            accessibility=lambda: True,
            input_monitoring=lambda: True,
            microphone=lambda: "authorized",
            secure_input=lambda: None,
            who="Terminal",
        )
        values.update(overrides)
        return {c.name: c for c in permissions.checks(**values)}

    def test_all_granted(self) -> None:
        found = self._checks()
        assert all(c.ok for c in found.values())
        assert set(found) == {"accessibility", "input monitoring", "microphone"}

    def test_missing_accessibility_names_the_switch_and_the_app(self) -> None:
        found = self._checks(accessibility=lambda: False, input_monitoring=lambda: False)
        check = found["accessibility"]
        assert check.ok is False
        assert "Privacy & Security → Accessibility" in check.fix and "Terminal" in check.fix
        assert "drops every keystroke" in check.detail
        assert found["input monitoring"].ok is False
        assert "Input Monitoring" in found["input monitoring"].fix

    def test_accessibility_covers_input_monitoring(self) -> None:
        found = self._checks(input_monitoring=lambda: False)
        assert found["input monitoring"].ok is True
        assert "covered by Accessibility" in found["input monitoring"].detail

    @pytest.mark.parametrize("status, ok", [("denied", False), ("restricted", False),
                                            ("not determined", None), (None, None)])
    def test_microphone(self, status, ok) -> None:
        check = self._checks(microphone=lambda: status)["microphone"]
        assert check.ok is ok
        if status == "denied":
            assert "Privacy & Security → Microphone" in check.fix and "NO SIGNAL" in check.detail

    def test_secure_input_is_reported_with_its_owner(self) -> None:
        check = self._checks(secure_input=lambda: (321, "Terminal"))["secure input"]
        assert check.ok is False and "Terminal" in check.detail and "Secure Keyboard Entry" in check.fix

    def test_cannot_tell(self) -> None:
        found = self._checks(accessibility=lambda: None, input_monitoring=lambda: None)
        assert found["accessibility"].ok is None and found["input monitoring"].ok is None


class TestSecureInputOwner:
    def test_parses_ioreg(self) -> None:
        def run(command, **_kw):
            if command[0] == "ioreg":
                return SimpleNamespace(stdout='  "IOConsoleUsers" = ({"kCGSSessionSecureInputPID"=812,'
                                              '"kCGSSessionOnConsoleKey"=Yes})')
            return SimpleNamespace(stdout="/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal\n")

        assert permissions.secure_input_owner(run) == (812, "Terminal")

    def test_off(self) -> None:
        assert permissions.secure_input_owner(lambda *a, **k: SimpleNamespace(stdout="nothing")) is None
        assert permissions.secure_input_owner(
            lambda *a, **k: SimpleNamespace(stdout='"kCGSSessionSecureInputPID"=0')) is None

    def test_ioreg_missing(self) -> None:
        def run(*_a, **_k):
            raise FileNotFoundError("ioreg")

        assert permissions.secure_input_owner(run) is None


class TestOffMac:
    def test_everything_is_unknown_off_macos(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert permissions.accessibility_granted() is None
        assert permissions.input_monitoring_granted() is None
        assert permissions.post_event_granted() is None
        assert permissions.microphone_status() is None
        assert permissions.secure_input_owner() is None
        assert permissions.main([]) == 1

    def test_notify_missing_once_per_component(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(permissions, "_notified", set())
        with mock.patch.object(permissions.subprocess, "run") as run:
            permissions.notify_missing("typing")
            permissions.notify_missing("typing")
            permissions.notify_missing("hotkey")
        assert run.call_count == 2
        script = run.call_args_list[0].args[0][2]
        assert "Accessibility" in script and script.startswith("display notification")

    def test_checklist_names_all_three(self) -> None:
        text = "\n".join(permissions.checklist_lines())
        for word in ("Accessibility", "Input Monitoring", "Microphone", "voice-keyboard doctor"):
            assert word in text

    @pytest.mark.parametrize("platform, shown", [("darwin", True), ("linux", False)])
    def test_setup_walkthrough_ends_with_the_checklist_on_a_mac(self, monkeypatch, tmp_path,
                                                                platform, shown) -> None:
        from voice_keyboard.setup_wizard import Wizard

        monkeypatch.setattr(sys, "platform", platform)
        out: list = []
        wizard = Wizard(tmp_path / "config.toml", ask=lambda prompt: "", secret=lambda prompt: "",
                        out=out.append, detect=lambda extra=(): None)
        wizard.finish()
        assert ("Input Monitoring" in "\n".join(out)) is shown


class TestDoctorOnMac:
    def test_permission_findings(self, tmp_path) -> None:
        checks = [
            permissions.Check("accessibility", True, "granted to Terminal"),
            permissions.Check("input monitoring", False, "not granted", "turn it on"),
            permissions.Check("microphone", None, "not asked yet", "say yes"),
            permissions.Check("secure input", False, "Secure Keyboard Entry is on", "turn it off"),
        ]
        found = {f.label: f for f in doctor.check_macos_permissions(checks, agent=tmp_path / "none.plist")}
        assert "typing" not in found  # check_typing reports Accessibility
        assert found["hotkey"].status == doctor.FAIL and found["hotkey"].fix == "turn it on"
        assert found["mic access"].status == doctor.WARN
        assert found["secure input"].status == doctor.WARN
        assert "login agent" not in found

    def test_login_agent_needs_its_own_switches(self, tmp_path) -> None:
        import plistlib

        script = tmp_path / "voice-keyboard-daemon"
        script.write_text("#!/opt/venv/bin/python3\nprint('hi')\n")
        agent = tmp_path / "agent.plist"
        agent.write_bytes(plistlib.dumps({"ProgramArguments": [str(script)]}))
        found = {f.label: f for f in doctor.check_macos_permissions([], agent=agent)}
        assert found["login agent"].status == doctor.WARN
        assert "python3" in found["login agent"].fix

    def test_typing_check(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with mock.patch.object(permissions, "accessibility_granted", return_value=False), \
                mock.patch.object(permissions, "responsible_app", return_value="iTerm"):
            finding = doctor.check_typing()
        assert finding.status == doctor.FAIL and "iTerm" in finding.fix
        with mock.patch.object(permissions, "accessibility_granted", return_value=True):
            assert doctor.check_typing().status == doctor.OK
            assert doctor.check_focus_probe().status == doctor.OK
        with mock.patch.object(permissions, "accessibility_granted", return_value=None):
            assert doctor.check_typing().status == doctor.WARN

    def test_run_on_mac_lists_every_permission(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(doctor, "LAUNCH_AGENT", tmp_path / "none.plist")
        monkeypatch.setattr(permissions, "checks", lambda: [
            permissions.Check("accessibility", False, "no"),
            permissions.Check("input monitoring", False, "no", "fix it"),
            permissions.Check("microphone", True, "granted"),
        ])
        monkeypatch.setattr(permissions, "accessibility_granted", lambda: False)
        labels = [f.label for f in doctor.run(tmp_path / "config.toml", None)]
        assert labels == ["config", "typing", "hotkey", "mic access", "clipboard", "focus"]


class TestFeedback:
    def _feedback(self):
        notes, cues = [], []
        now = {"t": 0.0}
        fb = overlay.MacFeedback(notify=lambda t, b: notes.append(b), cue=cues.append,
                                 clock=lambda: now["t"])
        return fb, notes, cues, now

    def test_a_dictation_is_two_tones_and_no_banners(self) -> None:
        fb, notes, cues, _ = self._feedback()
        fb.show("starting")
        for word in range(40):  # the live caption, several times a second
            fb.show("listening", f"▁▂▃ word {word}")
        fb.show("processing")
        fb.show("inserted", "Inserted 120 characters")
        assert notes == [] and cues == ["listen", "captured"]

    def test_failures_get_one_banner(self) -> None:
        fb, notes, cues, now = self._feedback()
        fb.show("listening")
        fb.show("empty", "Mic sent pure silence — System Settings › Privacy & Security › Microphone")
        fb.show("error", "Mic sent pure silence — System Settings › Privacy & Security › Microphone")
        assert len(notes) == 1 and "Microphone" in notes[0]
        now["t"] = overlay.DEDUPE_S + 1
        fb.show("error", "Mic sent pure silence — System Settings › Privacy & Security › Microphone")
        assert len(notes) == 2
        fb.show("error", "")
        assert notes[-1] == "Something went wrong"
        assert cues == ["listen", "captured"]

    def test_client_routes_macos_overlay_calls_here(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        shown = []
        monkeypatch.setattr(overlay, "show", lambda state, **kw: shown.append((state, kw.get("detail"))))
        monkeypatch.setattr(overlay, "hide", lambda: shown.append(("hide", None)))
        with mock.patch.object(client.subprocess, "run") as run, \
                mock.patch.object(client, "_focused_anchor") as anchor:
            for _ in range(10):
                client._show_overlay("listening", detail="▁▂ hello")
            client._show_overlay("error", detail="boom")
            client._stop_overlay()
            client._set_overlay_button(True)
        run.assert_not_called()  # no gdbus, no osascript per caption
        anchor.assert_not_called()  # no focus probe per caption either
        assert shown[-2:] == [("error", "boom"), ("hide", None)]
        assert len(shown) == 12

    def test_start_hint_mentions_the_foreground_daemon(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        hint = client._daemon_start_hint()
        assert "launchctl kickstart" in hint and "voice-keyboard-daemon" in hint
