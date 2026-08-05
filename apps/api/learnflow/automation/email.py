"""Email adapters. Mock by default so local dev and CI never send real mail."""

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
    to: str
    subject: str
    body: str
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class EmailAdapter(Protocol):
    name: str

    async def send(self, to: str, subject: str, body: str) -> SentEmail: ...


class MockEmailAdapter:
    """Logs and retains the last 200 messages so the UI/tests can assert on them."""

    name = "mock"

    def __init__(self) -> None:
        self.outbox: list[SentEmail] = []

    async def send(self, to: str, subject: str, body: str) -> SentEmail:
        message = SentEmail(to=to, subject=subject, body=body)
        self.outbox.append(message)
        del self.outbox[:-200]
        log.info("email_mock_sent", to=to, subject=subject, chars=len(body))
        return message


class SMTPEmailAdapter:
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
    global _adapter
    if _adapter is None:
        _adapter = SMTPEmailAdapter() if settings.smtp_host else MockEmailAdapter()
    return _adapter
