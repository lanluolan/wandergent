"""Tests for the mail layer.

Two things matter here and neither is "does the message arrive": that a service with no
mail provider still works and says so, and that a provider having a bad day cannot be
turned into an answer the reset endpoint is carefully designed not to give.
"""

import smtplib

import pytest

from app.config import settings
from app.mail import ConsoleMailer, SmtpMailer, build_mailer


def test_no_smtp_configured_gives_the_console_mailer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "")

    assert isinstance(build_mailer(), ConsoleMailer)


def test_a_configured_host_gives_the_real_one(monkeypatch) -> None:
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")

    assert isinstance(build_mailer(), SmtpMailer)


def test_the_console_mailer_is_loud(caplog) -> None:
    """It has to be findable in a log, because a server quietly printing reset codes to
    stdout while believing it sends email is a security problem, not a convenience."""
    ConsoleMailer().send("yu@example.com", "reset", "code is ABCD1234")

    assert "NO SMTP CONFIGURED" in caplog.text
    assert "ABCD1234" in caplog.text


def test_a_broken_mail_server_does_not_raise(monkeypatch, caplog) -> None:
    """The reset endpoint answers 204 whether or not the address exists. If a delivery
    failure raised, a 500 would tell the caller the address *did* exist -- turning the
    one thing that endpoint refuses to reveal into an error code."""

    def explode(*args, **kwargs):
        raise smtplib.SMTPException("mail server is having a day")

    monkeypatch.setattr(smtplib, "SMTP", explode)
    mailer = SmtpMailer("smtp.example.com", 587, "u", "p", "from@example.com")

    mailer.send("yu@example.com", "reset", "code is ABCD1234")

    assert "could not send mail" in caplog.text


def test_a_refused_connection_does_not_raise(monkeypatch) -> None:
    def explode(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", explode)

    SmtpMailer("smtp.example.com", 587, "", "", "from@example.com").send("a@b.c", "s", "b")


@pytest.mark.parametrize("port,expected", [(465, "SMTP_SSL"), (587, "SMTP"), (25, "SMTP")])
def test_the_port_chooses_implicit_or_negotiated_tls(monkeypatch, port, expected) -> None:
    """465 is implicit TLS and everything else negotiates it. Picked from the port rather
    than a setting: those are the two deployments that exist, and a third knob is a third
    way to end up sending credentials in the clear."""
    used: list[str] = []

    class Fake:
        def __init__(self, *args, **kwargs):
            used.append(type(self).__name__)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self):
            used.append("starttls")

        def login(self, *args):
            pass

        def send_message(self, message):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", type("SMTP_SSL", (Fake,), {}))
    monkeypatch.setattr(smtplib, "SMTP", type("SMTP", (Fake,), {}))

    SmtpMailer("smtp.example.com", port, "u", "p", "from@example.com").send("a@b.c", "s", "b")

    assert used[0] == expected
    assert ("starttls" in used) == (port != 465)
