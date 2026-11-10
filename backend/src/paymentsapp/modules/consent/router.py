"""Customer API: notices and consents (plan M9)."""

from datetime import datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from paymentsapp.api.deps import CurrentCustomer, DbSession
from paymentsapp.modules.consent import service
from paymentsapp.modules.identity.schemas import NoticeOut

router = APIRouter(prefix="/v1", tags=["customer"])


class ConsentOut(BaseModel):
    notice: NoticeOut
    granted: bool
    accepted_version: str | None
    granted_at: datetime | None
    needs_acceptance: bool


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notice_version: str = Field(min_length=1, max_length=32)


@router.get("/notices", response_model=list[NoticeOut])
async def notices() -> list[NoticeOut]:
    """Public: the texts a new customer reads before creating an account."""
    return [NoticeOut(**n.__dict__) for n in service.notices().values()]


@router.get("/consents", response_model=list[ConsentOut])
async def list_consents(customer: CurrentCustomer, session: DbSession) -> list[ConsentOut]:
    return [
        ConsentOut(
            notice=NoticeOut(**s.notice.__dict__),
            granted=s.granted,
            accepted_version=s.accepted_version,
            granted_at=s.granted_at,
            needs_acceptance=s.needs_acceptance,
        )
        for s in await service.statuses(session, customer.customer.id)
    ]


@router.post("/consents/{purpose}", status_code=status.HTTP_204_NO_CONTENT)
async def grant(purpose: str, body: Grant, customer: CurrentCustomer, session: DbSession) -> None:
    await service.grant(session, customer.customer.id, customer.actor, purpose, body.notice_version)
    await session.commit()


@router.delete("/consents/{purpose}", status_code=status.HTTP_204_NO_CONTENT)
async def withdraw(purpose: str, customer: CurrentCustomer, session: DbSession) -> None:
    await service.withdraw(session, customer.customer.id, customer.actor, purpose)
    await session.commit()
