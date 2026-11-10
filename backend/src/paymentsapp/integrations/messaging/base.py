"""Messaging interface (plan M13): how the app sends OTPs and alerts to customers.

Development uses the dev_messages plugin, which sends nothing real. Production plugs in an SMS provider with a
TRAI DLT-registered sender and templates, and an email provider, behind the same calls.
"""

from typing import Literal, Protocol

Purpose = Literal["otp", "security_alert", "transaction_alert"]


class MessagingUnavailable(Exception):
    """No messaging plugin is enabled for this environment, so nothing can be sent."""


class MessagingProvider(Protocol):
    async def send_sms(self, to: str, text: str, purpose: Purpose) -> None: ...

    async def send_email(self, to: str, subject: str, text: str, purpose: Purpose) -> None: ...
