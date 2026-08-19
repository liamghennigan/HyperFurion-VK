"""Operator CLI: issue, revoke, and inspect subscriber keys.

This is the sponsors-first path — hand-issue keys before any Stripe
automation exists:

    python -m hyperfurion_relay.admin issue --tier basic --email a@b.c
    python -m hyperfurion_relay.admin list
    python -m hyperfurion_relay.admin revoke --id 3
    python -m hyperfurion_relay.admin restore --id 3
"""

import argparse
import datetime
import os

from .db import PERIOD_SECONDS, Store
from .tiers import TIERS


def _store() -> Store:
    return Store(os.environ.get("RELAY_DB", "relay.db"))


def cmd_issue(args: argparse.Namespace) -> None:
    store = _store()
    user_id, api_key = store.create_user(tier=args.tier, email=args.email)
    print(f"user {user_id} ({args.tier}) — key shown once:")
    print(api_key)
    store.close()


def cmd_list(_args: argparse.Namespace) -> None:
    store = _store()
    rows = store.list_users()
    if not rows:
        print("no subscribers yet")
    for row in rows:
        resets = datetime.datetime.fromtimestamp(
            row["period_start"] + PERIOD_SECONDS, tz=datetime.timezone.utc
        ).date()
        print(
            f"#{row['id']:<4} …{row['key_hint']}  {row['tier']:<6} {row['status']:<8}"
            f" stt {row['stt_seconds_used'] / 3600:6.2f} h  tts {row['tts_chars_used']:>9,}"
            f"  resets {resets}  {row['email']}"
        )
    store.close()


def cmd_set_status(args: argparse.Namespace, status: str) -> None:
    store = _store()
    store.set_status(args.id, status)
    print(f"user {args.id}: {status}")
    store.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="hyperfurion-relay-admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue", help="issue a new subscriber key")
    issue.add_argument("--tier", choices=sorted(TIERS), default="basic")
    issue.add_argument("--email", default="")
    issue.set_defaults(func=cmd_issue)

    listing = sub.add_parser("list", help="list subscribers and usage")
    listing.set_defaults(func=cmd_list)

    revoke = sub.add_parser("revoke", help="revoke a subscriber")
    revoke.add_argument("--id", type=int, required=True)
    revoke.set_defaults(func=lambda a: cmd_set_status(a, "revoked"))

    restore = sub.add_parser("restore", help="re-activate a subscriber")
    restore.add_argument("--id", type=int, required=True)
    restore.set_defaults(func=lambda a: cmd_set_status(a, "active"))

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
