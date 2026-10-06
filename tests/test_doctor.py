from voice_keyboard import doctor


def test_render_marks_and_fixes() -> None:
    text = doctor.render([
        doctor.Finding(doctor.OK, "config", "/x/config.toml"),
        doctor.Finding(doctor.FAIL, "typing", "/dev/uinput is missing", "sudo modprobe uinput"),
        doctor.Finding(doctor.WARN, "clipboard", "no xclip", "install xclip"),
    ])
    assert "✓ config" in text and "✗ typing" in text and "! clipboard" in text
    assert "→ sudo modprobe uinput" in text
    assert "1 problem(s) to fix, 1 warning(s)." in text


def test_all_good_says_so() -> None:
    assert "Everything checks out." in doctor.render([doctor.Finding(doctor.OK, "config", "x")])


def test_missing_config_is_a_failure_with_the_fix(tmp_path) -> None:
    finding = doctor.check_config(tmp_path / "config.toml")
    assert finding.status == doctor.FAIL and "voice-keyboard setup" in finding.fix


def test_a_broken_check_never_hides_the_others(tmp_path, monkeypatch) -> None:
    def boom():
        raise RuntimeError("kaput")

    monkeypatch.setattr(doctor, "check_typing", boom)
    findings = doctor.run(tmp_path / "config.toml", None)
    labels = [f.label for f in findings]
    assert "clipboard" in labels and "focus" in labels
    assert any("kaput" in f.detail for f in findings)


def test_device_name_must_match_one_device(monkeypatch) -> None:
    import voice_keyboard.client as client

    devices = [
        {"name": "USB Mic", "channels": 1, "rate": 48000, "default": False},
        {"name": "Built-in Mic", "channels": 2, "rate": 44100, "default": True},
    ]
    monkeypatch.setattr(client, "_list_input_devices", lambda: devices)
    assert doctor.check_audio({"audio": {"device_name": "usb"}}).status == doctor.OK
    assert doctor.check_audio({"audio": {"device_name": "mic"}}).status == doctor.FAIL  # two match
    assert doctor.check_audio({"audio": {}}).detail == "Built-in Mic"
