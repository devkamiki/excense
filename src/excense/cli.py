"""Command line interface: `excense auth|sync|sync-loop|serve|user-add|status`."""
from __future__ import annotations

import argparse
import getpass
import logging
import sys

import bcrypt

from excense import ExcenseError
from excense.auth import MsAuth
from excense.config import Settings
from excense.graph.client import GraphClient


def cmd_auth(settings: Settings, args) -> int:
    MsAuth(settings).device_code()
    print("OK: signed in, token cached.")
    return 0


def cmd_auth_web(settings: Settings, args) -> int:
    auth = MsAuth(settings)
    flow = auth.auth_code_url()
    print("1. Open this URL in a browser (on any device that has one):")
    print(flow["auth_uri"])
    print("2. Sign in and approve. The page then fails to load at the")
    print("   localhost redirect — that is expected. Copy the full URL")
    print("   from the browser address bar.")
    pasted = input("3. Paste that URL here: ").strip()
    auth.complete_auth_code(flow, pasted)
    print("OK: signed in, token cached.")
    return 0


def cmd_sync(settings: Settings, args) -> int:
    from excense.bridge.engine import sync_once

    stats = sync_once(settings)
    for kind, s in stats.items():
        print(f"{kind}: remote={s['remote']} pulled={s['pulled']} deleted={s['deleted']}")
    return 0


def cmd_sync_loop(settings: Settings, args) -> int:
    from excense.bridge.engine import sync_loop

    sync_loop(settings)
    return 0


def cmd_serve(settings: Settings, args) -> int:
    from excense.server import serve

    return serve(settings)


def cmd_user_add(settings: Settings, args) -> int:
    settings.ensure_dirs()
    password = getpass.getpass(f"password for {args.username}: ")
    if not password:
        print("empty password rejected", file=sys.stderr)
        return 1
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    settings.passwd_path.touch(exist_ok=True)
    lines = [
        line for line in settings.passwd_path.read_text().splitlines()
        if line and not line.startswith(f"{args.username}:")
    ]
    lines.append(f"{args.username}:{hashed}")
    settings.passwd_path.write_text("\n".join(lines) + "\n")
    print(f"user '{args.username}' written to {settings.passwd_path}")
    return 0


def cmd_status(settings: Settings, args) -> int:
    auth = MsAuth(settings)
    if not auth.has_account():
        print("not signed in. Run: excense auth")
        return 1
    graph = GraphClient(auth, settings.graph_api)
    me = graph.me()
    print(f"hello {me.get('displayName')} <{me.get('userPrincipalName')}>")
    print(f"data dir : {settings.data_dir}")
    print(f"dav user : {settings.dav_user}")
    return 0


def parse_args(argv=None) -> tuple[Settings, argparse.Namespace]:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="excense")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth", help="interactive OAuth device-code sign-in")
    sub.add_parser(
        "auth-web",
        help="browser sign-in with paste-back, for tenants that block device-code",
    )
    sub.add_parser("sync", help="run one sync cycle")
    sub.add_parser("sync-loop", help="run the sync loop forever")
    sub.add_parser("serve", help="run the CalDAV/CardDAV server")
    sub.add_parser("status", help="print account / state info")

    p_user = sub.add_parser("user-add", help="create a DAV account (htpasswd)")
    p_user.add_argument("username")

    settings = Settings()
    return settings, parser.parse_args(argv)


def main(argv=None) -> int:
    try:
        settings, args = parse_args(argv)
    except ExcenseError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    commands = {
        "auth": cmd_auth,
        "auth-web": cmd_auth_web,
        "sync": cmd_sync,
        "sync-loop": cmd_sync_loop,
        "serve": cmd_serve,
        "user-add": cmd_user_add,
        "status": cmd_status,
    }
    try:
        return commands[args.command](settings, args)
    except ExcenseError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())