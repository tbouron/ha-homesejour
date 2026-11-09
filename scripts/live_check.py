"""Log in to homesejour.fr with the real client and print what it reads.

Usage: .venv/bin/python scripts/live_check.py
Credentials are prompted (hidden) and never stored or printed.
"""

from __future__ import annotations

import asyncio
import getpass
import sys
from collections import Counter
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.homesejour.client import HomeSejourClient  # noqa: E402


async def main() -> None:
    username = input("HomeSejour email: ").strip()
    password = getpass.getpass("Password: ")
    secret = getpass.getpass("TOTP secret (base32): ")
    async with aiohttp.ClientSession() as session:
        client = HomeSejourClient(session, username, password, secret)
        await client.async_login()
        print("Login OK")
        for prop in await client.async_get_properties():
            print(f"\nProperty {prop.id}: {prop.name} | ref {prop.reference} | {prop.location}")
            print("  states:", dict(Counter(b.state for b in prop.bookings)))
            for b in prop.bookings:
                print(
                    f"  {b.reference:<9} {b.state:<9} {b.start} -> {b.end} "
                    f"({b.nights} n) net={b.owner_net} origin={b.origin} added={b.added}"
                )


if __name__ == "__main__":
    asyncio.run(main())
