from __future__ import annotations

import html
import os
import smtplib
import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from typing import Callable
from urllib.parse import urljoin

from .content import SHOP_SHARE_URL


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str
    use_tls: bool

    @classmethod
    def from_environment(cls) -> "SMTPConfig | None":
        values = {name: os.getenv(name, "").strip() for name in (
            "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD",
            "SMTP_FROM_EMAIL", "SMTP_FROM_NAME", "SMTP_USE_TLS",
        )}
        if not all(values.values()):
            return None
        try:
            port = int(values["SMTP_PORT"])
        except ValueError:
            return None
        if not 1 <= port <= 65535:
            return None
        return cls(
            host=values["SMTP_HOST"], port=port,
            username=values["SMTP_USERNAME"], password=values["SMTP_PASSWORD"],
            from_email=values["SMTP_FROM_EMAIL"], from_name=values["SMTP_FROM_NAME"],
            use_tls=values["SMTP_USE_TLS"].lower() in {"1", "true", "yes", "on"},
        )


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    code: str


def public_link(path: str) -> str:
    base = os.getenv("PUBLIC_BASE_URL", SHOP_SHARE_URL).strip() or SHOP_SHARE_URL
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def build_message(*, to_email: str, subject: str, plain_text: str, html_body: str,
                  config: SMTPConfig, now: datetime | None = None) -> EmailMessage:
    timestamp = now or datetime.now(timezone.utc)
    message = EmailMessage()
    message["From"] = formataddr((config.from_name, config.from_email), charset="utf-8")
    message["To"] = to_email
    message["Subject"] = subject
    message["Date"] = format_datetime(timestamp)
    message["Message-ID"] = make_msgid(domain=config.from_email.partition("@")[2] or None)
    message.set_content(plain_text, charset="utf-8")
    message.add_alternative(html_body, subtype="html", charset="utf-8")
    return message


class EmailService:
    def __init__(self, config: SMTPConfig | None = None,
                 smtp_factory: Callable[..., object] | None = None):
        self.config = config if config is not None else SMTPConfig.from_environment()
        self.smtp_factory = smtp_factory or smtplib.SMTP

    def send(self, message: EmailMessage) -> DeliveryResult:
        if self.config is None:
            return DeliveryResult(False, "not_configured")
        try:
            with self.smtp_factory(self.config.host, self.config.port, timeout=10) as smtp:
                smtp.ehlo()
                if self.config.use_tls or self.config.port == 587:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                smtp.login(self.config.username, self.config.password)
                smtp.send_message(message)
            return DeliveryResult(True, "sent")
        except smtplib.SMTPAuthenticationError:
            return DeliveryResult(False, "authentication_failed")
        except smtplib.SMTPRecipientsRefused:
            return DeliveryResult(False, "recipient_rejected")
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected):
            return DeliveryResult(False, "connection_failed")
        except (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError):
            return DeliveryResult(False, "connection_failed")
        except smtplib.SMTPException:
            return DeliveryResult(False, "delivery_failed")
        except OSError:
            return DeliveryResult(False, "connection_failed")

    def send_invitation(self, *, recipient: str, student_name: str, role: str,
                        acceptance_url: str) -> DeliveryResult:
        if self.config is None:
            return DeliveryResult(False, "not_configured")
        role_text = role.replace("_", " ").title()
        plain = (
            "Woodshed Woodchuck\n\n"
            f"{student_name} invited you to be their trusted verifier.\n"
            f"Verifier role: {role_text}\n\nAccept the invitation: {acceptance_url}\n\n"
            "The invitation link may expire and can only be used as currently defined. "
            "Please do not forward this private link."
        )
        safe_name, safe_role, safe_url = html.escape(student_name), html.escape(role_text), html.escape(acceptance_url, quote=True)
        body = (
            f"<h1>Woodshed Woodchuck</h1><p><strong>{safe_name}</strong> invited you to be their trusted verifier.</p>"
            f"<p>Verifier role: {safe_role}</p><p><a href=\"{safe_url}\">Accept the invitation</a></p>"
            "<p>The invitation link may expire and can only be used as currently defined. Please do not forward this private link.</p>"
        )
        return self.send(build_message(
            to_email=recipient,
            subject="You have been invited to verify a Woodshed Woodchuck student",
            plain_text=plain, html_body=body, config=self.config,
        ))

    def send_practice_chart(self, *, recipient: str, student_name: str,
                            practice_date: str, minutes: int, role: str,
                            review_url: str) -> DeliveryResult:
        if self.config is None:
            return DeliveryResult(False, "not_configured")
        role_text = role.replace("_", " ").title()
        plain = (
            "Woodshed Woodchuck\n\n"
            f"{student_name}'s P-Chart is ready for review.\nPractice date: {practice_date}\n"
            f"Practice minutes: {minutes}\nVerifier role: {role_text}\n\nReview securely: {review_url}\n\n"
            "Sign in to approve the chart or reject it with a note. Please do not forward this private review link."
        )
        safe = [html.escape(str(value), quote=True) for value in (student_name, practice_date, minutes, role_text, review_url)]
        body = (
            f"<h1>Woodshed Woodchuck</h1><p><strong>{safe[0]}</strong>'s P-Chart is ready for review.</p>"
            f"<p>Practice date: {safe[1]}<br>Practice minutes: {safe[2]}<br>Verifier role: {safe[3]}</p>"
            f"<p><a href=\"{safe[4]}\">Review the P-Chart securely</a></p>"
            "<p>Sign in to approve the chart or reject it with a note. Please do not forward this private review link.</p>"
        )
        return self.send(build_message(
            to_email=recipient, subject="A Woodshed Woodchuck P-Chart is ready for your review",
            plain_text=plain, html_body=body, config=self.config,
        ))
