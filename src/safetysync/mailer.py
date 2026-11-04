"""Envoi d'emails HTML par SMTP (relais Brevo, port 587 avec STARTTLS ; 465 = SSL direct)."""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from safetysync.config import SmtpSettings

logger = logging.getLogger(__name__)


class SmtpMailer:
    def __init__(self, settings: SmtpSettings) -> None:
        self.settings = settings

    def build(
        self,
        to: list[str],
        subject: str,
        html: str,
        cc: list[str] | None = None,
        images: dict[str, bytes] | None = None,
    ) -> EmailMessage:
        """`images` : PNG intégrés, référencés dans le HTML par `cid:<nom>`."""
        if not to:
            raise ValueError("aucun destinataire")
        cc = [a for a in (cc or []) if a not in to]
        msg = EmailMessage()
        msg["From"] = formataddr((self.settings.email_from_name, self.settings.email_from))
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self.settings.email_from.split("@")[-1])
        msg.set_content(_text_version(html))
        msg.add_alternative(html, subtype="html")
        if images:
            html_part = msg.get_payload()[1]
            for name, content in images.items():
                html_part.add_related(
                    content,
                    maintype="image",
                    subtype="png",
                    cid=f"<{name}>",
                    filename=f"{name}.png",
                    disposition="inline",
                )
        return msg

    def send(
        self,
        to: list[str],
        subject: str,
        html: str,
        cc: list[str] | None = None,
        images: dict[str, bytes] | None = None,
    ) -> None:
        msg = self.build(to, subject, html, cc, images)
        s = self.settings
        context = ssl.create_default_context()
        if s.smtp_port == 465:
            server = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds, context=context
            )
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds)
        with server:
            if s.smtp_port != 465:
                server.starttls(context=context)
            server.login(s.smtp_user, s.smtp_password.get_secret_value())
            server.send_message(msg)
        logger.info("Email « %s » envoyé à %s (cc : %s)", subject, msg["To"], msg["Cc"] or "-")


def _text_version(html: str) -> str:
    """Version texte minimale (certains clients et filtres anti-spam l'exigent)."""
    text = re.sub(r"<head>.*?</head>", "", html, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<(br|/p|/tr|/h\d|/li)\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"[ \t]+", " ", text).strip()
