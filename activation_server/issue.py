"""Seller CLI — issue/revoke/list license keys.

  python -m activation_server.issue --key CUST-123 --seats 2 --days 365
  python -m activation_server.issue --key CUST-123 --revoke
  python -m activation_server.issue --list

DB path via ACTIVATION_DB (default ./activation.db). Keys are whatever string
you choose (e.g. a UUID); never commit them anywhere.
"""
from __future__ import annotations

import argparse
import asyncio
import os

from activation_server.store import ActivationStore


async def _run(args: argparse.Namespace) -> int:
    store = ActivationStore(os.environ.get("ACTIVATION_DB", "activation.db"))
    await store.init()
    try:
        if args.list:
            for key, status, expires, used, total in await store.list_keys():
                print(f"{key}  {status:8}  expires={expires}  seats={used}/{total}")
            return 0
        if not args.key:
            print("--key is required (or --list)")
            return 2
        if args.revoke:
            ok = await store.revoke(args.key)
            print("revoked" if ok else "key not found")
            return 0 if ok else 1
        lic = await store.issue(args.key, seats=args.seats, days=args.days)
        expires = lic.expires_at.date().isoformat() if lic.expires_at else "never"
        print(f"issued {lic.key}: seats={lic.allowed_install_count} expires={expires}")
        return 0
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue/revoke activation keys")
    parser.add_argument("--key", help="license key string")
    parser.add_argument("--seats", type=int, default=1, help="allowed installs")
    parser.add_argument("--days", type=int, default=None, help="validity days (default: no expiry)")
    parser.add_argument("--revoke", action="store_true", help="revoke the key")
    parser.add_argument("--list", action="store_true", help="list all keys")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
