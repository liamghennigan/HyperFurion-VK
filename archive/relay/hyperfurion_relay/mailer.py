"""Pluggable one-time-code email delivery for the seamless login flow.

Transports, in order of preference — all env-driven, none a hard dependency:
  1. Resend HTTP API   (RESEND_API_KEY)      — recommended; one secret on Fly
  2. SMTP              (SMTP_HOST + creds)    — any mailbox you already own
  3. dev fallback      (neither configured)  — log the code at WARNING so the
                                               whole flow is testable before
                                               any transport is wired

A missing or failing transport degrades rather than throwing: the caller
(`/auth/request`) always answers the same generic 200, so email delivery
state is never observable to an unauthenticated caller (no user enumeration).
"""

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

import aiohttp

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"

_SUBJECT = "Your HyperFurion VK sign-in code"


def _body(code: str) -> str:
    return (
        "Your HyperFurion VK sign-in code is:\n\n"
        f"    {code}\n\n"
        "It expires in 10 minutes. Enter it where you ran "
        "`voice-keyboard login`.\n\n"
        "This code sets up (or restores) your key automatically — you never "
        "have to save it. Lose it and you can just log in again.\n\n"
        "Didn't request this? You can safely ignore this email."
    )


async def _send_resend(
    session: aiohttp.ClientSession, cfg: dict, to: str, code: str
) -> str:
    payload = {
        "from": cfg["email_from"],
        "to": [to],
        "subject": _SUBJECT,
        "text": _body(code),
    }
    headers = {"Authorization": f"Bearer {cfg['resend_api_key']}"}
    try:
        async with session.post(RESEND_ENDPOINT, json=payload, headers=headers) as resp:
            if resp.status >= 400:
                detail = (await resp.text())[:200]
                return f"resend {resp.status}: {detail}"
    except aiohttp.ClientError as exc:
        return f"resend transport error: {exc}"
    return ""


def _send_smtp_blocking(cfg: dict, to: str, code: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg["email_from"]
    msg["To"] = to
    msg["Subject"] = _SUBJECT
    msg.set_content(_body(code))
    with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=15) as server:
        server.starttls(context=ssl.create_default_context())
        if cfg.get("smtp_user"):
            server.login(cfg["smtp_user"], cfg["smtp_pass"])
        server.send_message(msg)


async def send_login_code(
    session: aiohttp.ClientSession, cfg: dict, to: str, code: str
) -> str:
    """Deliver a sign-in code. Returns "" on success, or a short reason string
    (logged by the caller; never surfaced to the client)."""
    if cfg.get("resend_api_key"):
        return await _send_resend(session, cfg, to, code)
    if cfg.get("smtp_host"):
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, _send_smtp_blocking, cfg, to, code)
        except Exception as exc:  # smtplib raises a family of exceptions
            return f"smtp error: {exc}"
        return ""
    # No transport configured — log the code so the flow works end-to-end in
    # dev/test. Treated as success (the request still answers 200).
    logger.warning(
        "no email transport configured (set RESEND_API_KEY or SMTP_HOST) — "
        "login code for %s is %s",
        to,
        code,
    )
    return ""
