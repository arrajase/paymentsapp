"""Admin API, development only: the Dev inbox of messages the dev_messages plugin "sent" (OTPs, alerts).

Outside development every route here answers 404, as if it didn't exist.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from paymentsapp.api.deps import CurrentAdmin, DbSession, admin_ip_allowed
from paymentsapp.core.config import Environment, get_settings
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.devmessages.provider import find_adb, list_inbox


async def development_only() -> None:
    if get_settings().environment != Environment.DEVELOPMENT:
        raise AppError("NOT_FOUND", "Not Found", status_code=404)


router = APIRouter(
    prefix="/admin/v1/dev",
    dependencies=[Depends(development_only), Depends(admin_ip_allowed)],
    tags=["admin", "development"],
)


class DevMessageOut(BaseModel):
    id: int
    channel: str
    to_address: str
    purpose: str
    subject: str | None
    body: str
    pushed_to_emulator: bool
    created_at: datetime


class DevInboxOut(BaseModel):
    adb_found: bool
    messages: list[DevMessageOut]


@router.get("/messages", response_model=DevInboxOut)
async def messages(_: CurrentAdmin, session: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> DevInboxOut:
    rows = await list_inbox(session, limit)
    return DevInboxOut(
        adb_found=find_adb() is not None,
        messages=[
            DevMessageOut(
                id=m.id,
                channel=m.channel,
                to_address=m.to_address,
                purpose=m.purpose,
                subject=m.subject,
                body=m.body,
                pushed_to_emulator=m.pushed_to_emulator,
                created_at=m.created_at,
            )
            for m in rows
        ],
    )
