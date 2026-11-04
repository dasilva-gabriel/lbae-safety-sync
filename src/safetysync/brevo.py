"""Envoi d'emails via l'API transactionnelle Brevo (POST /smtp/email)."""

from __future__ import annotations

import logging

import httpx

from safetysync.config import BrevoSettings
from safetysync.http import HttpApi

logger = logging.getLogger(__name__)


class BrevoMailer(HttpApi):
    service = "Brevo"

    def __init__(self, settings: BrevoSettings, http: httpx.Client | None = None, **kw) -> None:
        super().__init__(
            settings.api_url,
            {"api-key": settings.api_key.get_secret_value(), "accept": "application/json"},
            timeout=settings.timeout_seconds,
            max_retries=2,
            http=http,
            **kw,
        )
        self.settings = settings

    def send(self, to: list[str], subject: str, html: str, cc: list[str] | None = None) -> None:
        if not to:
            raise ValueError("aucun destinataire")
        cc = [a for a in (cc or []) if a not in to]
        body = {
            "sender": {"email": self.settings.sender_email, "name": self.settings.sender_name},
            "to": [{"email": a} for a in to],
            "subject": subject,
            "htmlContent": html,
        }
        if cc:
            body["cc"] = [{"email": a} for a in cc]
        self.json("POST", "/smtp/email", json=body)
        logger.info(
            "Email « %s » envoyé à %s (cc : %s)", subject, ", ".join(to), ", ".join(cc) or "-"
        )
