"""Email sending adapters and in-memory test outbox.

===============================================================================
WHAT THIS FILE IS RESPONSIBLE FOR:
===============================================================================
This file provides email delivery mechanisms for automation workflows:
1. `EmailAdapter`: Abstract protocol defining `send(to, subject, body)`.
2. `MockEmailAdapter`: In-memory outbox retaining the last 200 sent emails,
   used during development and testing to prevent spamming real mailboxes.
3. `SMTPEmailAdapter`: Production SMTP client using `smtplib` and STARTTLS.
4. `get_email_adapter()`: Returns SMTP if `SMTP_HOST` is set, otherwise Mock.

===============================================================================
HOW IT CONNECTS TO THE REST OF THE PROJECT:
===============================================================================
- `automation/runners.py`: Calls `get_email_adapter().send()` during learner onboarding,
  inactivity reminder sweeps, and weekly instructor summaries.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Protocol

from ..config import settings
from ..logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class SentEmail:
    """Represents a successfully dispatched email notification."""
    to: str
    subject: str
    body: str
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EmailAdapter(Protocol):
    """Protocol for transactional email delivery."""
    name: str

    async def send(self, to: str, subject: str, body: str) -> SentEmail: ...


class MockEmailAdapter:
    """In-memory outbox that logs messages and stores them in a circular buffer."""
    name = "mock"

    def __init__(self) -> None:
        self.outbox: list[SentEmail] = []

    async def send(self, to: str, subject: str, body: str) -> SentEmail:
        message = SentEmail(to=to, subject=subject, body=body)
        self.outbox.append(message)
        # Retain only the most recent 200 emails in memory
        del self.outbox[:-200]
        log.info("email_mock_sent", to=to, subject=subject, chars=len(body))
        return message


class SMTPEmailAdapter:
    """Production email adapter delivering emails via standard SMTP server."""
    name = "smtp"

    async def send(self, to: str, subject: str, body: str) -> SentEmail:
        import asyncio

        message = EmailMessage()
        message["From"] = settings.email_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        def _send() -> None:
            with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if settings.smtp_user:
                    smtp.login(settings.smtp_user, settings.smtp_password or "")
                smtp.send_message(message)

        await asyncio.to_thread(_send)
        log.info("email_smtp_sent", to=to, subject=subject)
        return SentEmail(to=to, subject=subject, body=body)


_adapter: EmailAdapter | None = None


def get_email_adapter() -> EmailAdapter:
    """Returns the global active email adapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = SMTPEmailAdapter() if settings.smtp_host else MockEmailAdapter()
    return _adapter
