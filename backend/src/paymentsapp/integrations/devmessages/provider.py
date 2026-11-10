"""The dev_messages plugin: a stand-in for real SMS and email during development.

Every message is stored for the admin website's Dev inbox. An SMS is also pushed into a running Android emulator
with `adb emu sms send`, so it appears in the emulator's Messages app as it would on a phone. If adb or the
emulator isn't available, the inbox still has the message.
"""

import asyncio
import logging
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import get_settings
from paymentsapp.integrations.devmessages.models import DevMessage
from paymentsapp.integrations.messaging.base import Purpose

log = logging.getLogger(__name__)

EMULATOR_SENDER = "5556"  # the "phone number" the SMS appears to come from
INBOX_RETENTION = timedelta(days=1)


def find_adb() -> str | None:
    return get_settings().adb_path or shutil.which("adb")


async def push_sms_to_emulator(to: str, text: str) -> bool:
    """Deliver a fake SMS into a running emulator. Returns False if there's no adb or no emulator."""
    adb = find_adb()
    if adb is None:
        return False
    # A thread rather than asyncio's subprocess support: on Windows, uvicorn --reload runs a selector event loop,
    # where asyncio.create_subprocess_exec raises NotImplementedError.
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [adb, "emu", "sms", "send", EMULATOR_SENDER, text],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = (result.stdout + result.stderr).decode(errors="replace").lower()
    return result.returncode == 0 and "error" not in output and "no emulators" not in output


class DevMessagingProvider:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def send_sms(self, to: str, text: str, purpose: Purpose) -> None:
        pushed = await push_sms_to_emulator(to, text)
        self._session.add(
            DevMessage(channel="sms", to_address=to, purpose=purpose, body=text, pushed_to_emulator=pushed)
        )
        await self._session.flush()

    async def send_email(self, to: str, subject: str, text: str, purpose: Purpose) -> None:
        self._session.add(DevMessage(channel="email", to_address=to, purpose=purpose, subject=subject, body=text))
        await self._session.flush()


async def list_inbox(session: AsyncSession, limit: int = 50) -> list[DevMessage]:
    """Newest first. Old messages are removed first, so test OTPs don't pile up."""
    await session.execute(delete(DevMessage).where(DevMessage.created_at < datetime.now(UTC) - INBOX_RETENTION))
    await session.commit()
    return list((await session.scalars(select(DevMessage).order_by(DevMessage.id.desc()).limit(limit))).all())
