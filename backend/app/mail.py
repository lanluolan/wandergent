"""Sending mail, and working without it.

Password reset needs a channel that proves someone owns the account, and that channel is
email. But requiring a mail provider to *run* the service would mean the whole reset flow
could not be developed or tested without one, so there are two implementations behind one
interface: SMTP when it is configured, and the log when it is not.

**Standard library `smtplib`**, no dependency. Every provider speaks SMTP, and the
alternative -- an SDK per provider -- is a dependency and a lock-in for a feature that
sends one kind of message.

**Sending never raises.** A reset request that 500s because the mail server hiccupped tells
the caller their account is broken when it is not, and -- worse -- tells an attacker
probing addresses something the endpoint is carefully designed not to reveal. Failures are
logged and swallowed; the endpoint answers the same either way.
"""

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    """Writes the message to the log instead of sending it.

    The default, because it makes the reset flow work end to end on a laptop with no
    account anywhere. It is also the reason the boot warning exists: a server that thinks
    it is emailing people and is in fact printing their reset codes to stdout is a
    security problem, not a convenience.
    """

    def send(self, to: str, subject: str, body: str) -> None:
        logger.warning("NO SMTP CONFIGURED -- mail not sent. To: %s | %s\n%s", to, subject, body)


class SmtpMailer:
    """Sends over SMTP, with STARTTLS unless the port says implicit TLS."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        timeout: float = 10.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._timeout = timeout

    def send(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)

        try:
            # 465 is implicit TLS, everything else is STARTTLS. Picking on the port rather
            # than adding a setting: those are the two deployments that exist, and a
            # third option nobody uses is a third way to misconfigure it.
            if self._port == 465:
                with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout) as server:
                    self._deliver(server, message)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
                    server.starttls()
                    self._deliver(server, message)
        except (OSError, smtplib.SMTPException) as exc:
            # Swallowed on purpose -- see the module docstring. The caller must not be
            # able to tell a delivery failure from a successful send.
            logger.error("could not send mail to %s: %s", to, exc)

    def _deliver(self, server: smtplib.SMTP, message: EmailMessage) -> None:
        if self._username:
            server.login(self._username, self._password)
        server.send_message(message)


def build_mailer() -> Mailer:
    """The mailer this deployment is configured for."""
    if not settings.smtp_host:
        return ConsoleMailer()
    return SmtpMailer(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        sender=settings.smtp_from or settings.smtp_username,
    )


mailer: Mailer = build_mailer()
