from __future__ import annotations

import os
import secrets
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

OTP_TTL_MINUTES = int(os.getenv('OTP_TTL_MINUTES', '10'))

@dataclass(frozen=True)
class DeliveryResult:
    channel: str
    delivered: bool
    provider: str
    message: str = ''


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def smtp_configured() -> bool:
    return bool(os.getenv('SMTP_HOST') and os.getenv('EMAIL_FROM'))


def send_email(to: str, subject: str, body: str) -> DeliveryResult:
    host = os.getenv('SMTP_HOST')
    if not host:
        return DeliveryResult('email', False, 'none', 'SMTP is not configured')
    port = int(os.getenv('SMTP_PORT', '587'))
    username = os.getenv('SMTP_USERNAME')
    password = os.getenv('SMTP_PASSWORD')
    sender = os.getenv('EMAIL_FROM')
    use_tls = os.getenv('SMTP_TLS', 'true').lower() == 'true'
    message = EmailMessage()
    message['From'] = sender
    message['To'] = to
    message['Subject'] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            if use_tls:
                server.starttls()
            if username:
                server.login(username, password or '')
            server.send_message(message)
        return DeliveryResult('email', True, 'smtp', 'Delivered')
    except Exception as exc:
        return DeliveryResult('email', False, 'smtp', str(exc))


def send_push(*args: Any, **kwargs: Any) -> DeliveryResult:
    return DeliveryResult('push', False, 'none', 'Push provider is not configured')


def send_sms(*args: Any, **kwargs: Any) -> DeliveryResult:
    return DeliveryResult('sms', False, 'none', 'SMS provider is not configured')
