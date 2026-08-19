"""`voice-keyboard login` is archived — it must not look live."""

import io
from contextlib import redirect_stderr

import pytest

from voice_keyboard.client import _run_login


def test_login_refuses_without_contacting_a_host() -> None:
    err = io.StringIO()
    with redirect_stderr(err), pytest.raises(SystemExit) as exc:
        _run_login({}, ["someone@example.com"])
    assert exc.value.code == 2
    text = err.getvalue()
    assert "not available" in text
    assert "api.hyperfurion.com" in text
    assert "subscription" in text.lower()
