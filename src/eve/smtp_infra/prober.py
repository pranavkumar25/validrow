"""The raw SMTP mailbox probe (Layer 6).

Conversation: connect MX:25 -> EHLO -> MAIL FROM -> RCPT TO -> read code -> QUIT.

**We never issue DATA.** The whole point is to ask "would you accept mail for
this address?" without ever sending a message. ``probe_rcpt`` returns the RCPT
response code; interpretation (valid/invalid/greylist/provider-unreliable) is
done by the service using the provider strategy.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import aiosmtplib


@dataclass
class SmtpReply:
    code: int
    message: str
    # Proof, for tests and audits, that the probe never transmitted a message body.
    sent_data: bool = False


async def probe_rcpt(
    mx_host: str,
    recipient: str,
    *,
    port: int = 25,
    helo: str = "verifier.local",
    mail_from: str = "verify@verifier.local",
    timeout: float = 15.0,
    source_ip: Optional[str] = None,
) -> SmtpReply:
    """Run the RCPT probe against one MX host. Never sends DATA."""
    kwargs = dict(hostname=mx_host, port=port, timeout=timeout, local_hostname=helo)
    if source_ip:
        kwargs["source_address"] = (source_ip, 0)

    client = aiosmtplib.SMTP(**kwargs)
    code, message = 0, ""
    try:
        await client.connect()
        try:
            await client.ehlo()
        except aiosmtplib.SMTPResponseException:
            await client.helo()
        code, message = await _mail_then_rcpt(client, mail_from, recipient)
    except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPConnectTimeoutError):
        code, message = 0, "connect_error"
    except (asyncio.TimeoutError, aiosmtplib.SMTPTimeoutError):
        code, message = 0, "timeout"
    except (ConnectionError, OSError) as exc:
        code, message = 0, f"conn_error:{type(exc).__name__}"
    except aiosmtplib.SMTPException as exc:
        code, message = getattr(exc, "code", 0) or 0, f"smtp_error:{type(exc).__name__}"
    finally:
        await _safe_quit(client)

    return SmtpReply(code=int(code or 0), message=str(message), sent_data=False)


async def _mail_then_rcpt(client: aiosmtplib.SMTP, mail_from: str, recipient: str):
    try:
        await client.mail(mail_from)
    except aiosmtplib.SMTPResponseException as exc:
        return exc.code, str(exc.message)
    try:
        resp = await client.rcpt(recipient)
        return resp.code, str(resp.message)
    except aiosmtplib.SMTPResponseException as exc:
        return exc.code, str(exc.message)


async def _safe_quit(client: aiosmtplib.SMTP) -> None:
    try:
        await client.quit()
    except Exception:  # noqa: BLE001 - best-effort teardown
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
