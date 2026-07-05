"""Seamless email-verified login: request a code, redeem it for a fresh key.

The whole point is that a lost key is a non-event — proving the subscription
email (re)issues the key on demand. These tests drive the real relay over
HTTP with a fake code sender injected via cfg, so no email is ever sent.
"""

import aiohttp

from harness import run_rig


def _install_code_capture(rig) -> list:
    """Swap the mailer for a capture list; returns the (email, code) sink."""
    sent: list = []

    async def _fake_send(session, cfg, email, code):
        sent.append((email, code))
        return ""

    rig.relay_cfg["send_login_code"] = _fake_send
    return sent


async def _request(session, base, email):
    async with session.post(f"{base}/auth/request", json={"email": email}) as resp:
        return resp.status, await resp.json()


async def _verify(session, base, email, code):
    async with session.post(
        f"{base}/auth/verify", json={"email": email, "code": code}
    ) as resp:
        return resp.status, await resp.json()


class TestLoginFlow:
    def test_request_then_verify_issues_working_key(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            uid, original = rig.store.create_user("basic", email="sub@example.com")
            async with aiohttp.ClientSession() as session:
                status, body = await _request(session, rig.http_base, "sub@example.com")
                assert status == 200 and body["status"] == "ok"
                assert len(sent) == 1
                email, code = sent[0]
                assert email == "sub@example.com" and len(code) == 6

                status, body = await _verify(session, rig.http_base, "sub@example.com", code)
                assert status == 200
                new_key = body["api_key"]
                assert new_key.startswith("hfk_")
                assert body["tier"] == "basic"

            # the reissued key resolves to the same subscriber...
            assert rig.store.lookup_key(new_key)["id"] == uid
            # ...and the key handed out at signup no longer authenticates.
            assert rig.store.lookup_key(original) is None

        run_rig(scenario)

    def test_wrong_code_is_rejected_then_correct_still_works(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            rig.store.create_user("pro", email="me@example.com")
            async with aiohttp.ClientSession() as session:
                await _request(session, rig.http_base, "me@example.com")
                _, code = sent[0]
                bad = "000000" if code != "000000" else "111111"
                status, _ = await _verify(session, rig.http_base, "me@example.com", bad)
                assert status == 400
                status, body = await _verify(session, rig.http_base, "me@example.com", code)
                assert status == 200 and body["api_key"].startswith("hfk_")

        run_rig(scenario)

    def test_code_is_single_use(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            rig.store.create_user("basic", email="once@example.com")
            async with aiohttp.ClientSession() as session:
                await _request(session, rig.http_base, "once@example.com")
                _, code = sent[0]
                status, _ = await _verify(session, rig.http_base, "once@example.com", code)
                assert status == 200
                status, _ = await _verify(session, rig.http_base, "once@example.com", code)
                assert status == 400  # already consumed

        run_rig(scenario)

    def test_unknown_email_is_silent_and_sends_nothing(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            async with aiohttp.ClientSession() as session:
                # Same 200 as a real subscriber — no enumeration...
                status, body = await _request(session, rig.http_base, "nobody@example.com")
                assert status == 200 and body["status"] == "ok"
                # ...and verifying an invented code fails.
                status, _ = await _verify(session, rig.http_base, "nobody@example.com", "123456")
                assert status == 400
            assert sent == []  # no code ever generated for a non-subscriber

        run_rig(scenario)

    def test_email_is_case_insensitive(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            rig.store.create_user("basic", email="Mixed@Example.com")
            async with aiohttp.ClientSession() as session:
                await _request(session, rig.http_base, "mixed@example.COM")
                assert len(sent) == 1
                _, code = sent[0]
                status, _ = await _verify(session, rig.http_base, "MIXED@example.com", code)
                assert status == 200

        run_rig(scenario)

    def test_resend_is_rate_limited(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            rig.store.create_user("basic", email="fast@example.com")
            async with aiohttp.ClientSession() as session:
                await _request(session, rig.http_base, "fast@example.com")
                await _request(session, rig.http_base, "fast@example.com")
            # Second request within the interval is throttled → still one email.
            assert len(sent) == 1

        run_rig(scenario)


class TestRevokedSubscriber:
    def test_revoked_subscription_cannot_log_in(self) -> None:
        async def scenario(rig):
            sent = _install_code_capture(rig)
            uid, _ = rig.store.create_user("basic", email="gone@example.com")
            rig.store.set_status(uid, "revoked")
            async with aiohttp.ClientSession() as session:
                status, _ = await _request(session, rig.http_base, "gone@example.com")
                assert status == 200  # generic answer either way
            assert sent == []  # inactive subscriber gets no code

        run_rig(scenario)
