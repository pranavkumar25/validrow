"""A tiny mock mail server for demoing SMTP verification locally.

It behaves like a real MX for a couple of demo domains so you can watch the
engine produce genuine valid / invalid / catch-all verdicts without needing
port-25-capable IPs or touching the real internet.

Rules:
  * acme-demo.com   -> accepts a known set of mailboxes, rejects the rest (550)
  * catchall-demo.com -> accepts EVERY recipient (a catch-all domain)
  * anything else   -> rejected

Point the engine at it with:
    EVE_ENABLE_SMTP=true EVE_ENABLE_DNS=false \\
    EVE_SMTP_TARGET_HOST=127.0.0.1 EVE_SMTP_TARGET_PORT=2525

Run:  python scripts/mock_mailserver.py
"""
from __future__ import annotations

import time

from aiosmtpd.controller import Controller

VALID_MAILBOXES = {
    "alice@acme-demo.com",
    "bob@acme-demo.com",
    "info@acme-demo.com",  # a real (role) mailbox — exists but is a role account
}
CATCH_ALL_DOMAINS = {"catchall-demo.com"}
PORT = 2525


class DemoHandler:
    def __init__(self) -> None:
        self.data_ever_received = False  # should always stay False

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        domain = address.split("@")[-1].lower()
        if domain in CATCH_ALL_DOMAINS or address.lower() in VALID_MAILBOXES:
            envelope.rcpt_tos.append(address)
            return "250 2.1.5 Recipient OK"
        return f"550 5.1.1 <{address}>: Recipient address rejected: User unknown"

    async def handle_DATA(self, server, session, envelope):
        # The verifier should never reach DATA — but be safe if it does.
        self.data_ever_received = True
        return "250 2.0.0 Ok"


def main() -> None:
    handler = DemoHandler()
    controller = Controller(handler, hostname="127.0.0.1", port=PORT)
    controller.start()
    print(f"Mock mailserver listening on 127.0.0.1:{PORT}", flush=True)
    print(f"  valid mailboxes : {sorted(VALID_MAILBOXES)}", flush=True)
    print(f"  catch-all domain: {sorted(CATCH_ALL_DOMAINS)}", flush=True)
    print("Ctrl-C to stop.", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        controller.stop()


if __name__ == "__main__":
    main()
