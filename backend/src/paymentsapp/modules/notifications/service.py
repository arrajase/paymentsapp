"""Messages to customers (plan M13): OTPs and security alerts, through the enabled messaging plugin.

Texts never include anything sensitive beyond what the message is for: no balances, account numbers or full
identifiers.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.integrations.registry import get_messaging

APP_NAME = "paymentsapp"


async def send_otp(session: AsyncSession, channel: str, destination: str, code: str, minutes_valid: int) -> None:
    messaging = await get_messaging(session)
    text = (
        f"{code} is your {APP_NAME} verification code. It expires in {minutes_valid} minutes. "
        "Never share it with anyone, including our staff."
    )
    if channel == "mobile":
        await messaging.send_sms(destination, text, "otp")
    else:
        await messaging.send_email(destination, f"Your {APP_NAME} code: {code}", text, "otp")


async def send_security_alert(session: AsyncSession, destinations: list[tuple[str, str]], text: str) -> None:
    """Send to every verified identifier of the customer: (channel, destination) pairs."""
    messaging = await get_messaging(session)
    message = f"{APP_NAME} security alert: {text} If this wasn't you, contact support immediately."
    for channel, destination in destinations:
        if channel == "mobile":
            await messaging.send_sms(destination, message, "security_alert")
        else:
            await messaging.send_email(destination, f"{APP_NAME} security alert", message, "security_alert")
