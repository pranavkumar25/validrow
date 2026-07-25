"""In-process fake SMTP server (aiosmtpd) for prober/service integration tests.

Lets us prove the full EHLO->MAIL->RCPT->QUIT conversation and the "never DATA"
guarantee against a real socket, with no port-25 access."""
from __future__ import annotations

import socket

from aiosmtpd.controller import Controller


class RecordingHandler:
    def __init__(self, valid=None, catch_all=False):
        self.valid = {a.lower() for a in (valid or [])}
        self.catch_all = catch_all
        self.data_received = False  # flips True only if a DATA body is ever sent

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if self.catch_all or address.lower() in self.valid:
            envelope.rcpt_tos.append(address)
            return "250 OK"
        return "550 5.1.1 No such user here"

    async def handle_DATA(self, server, session, envelope):
        self.data_received = True
        return "250 Message accepted"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start_server(handler) -> tuple[Controller, int]:
    port = _free_port()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    return controller, port
