"""Customer API: KYC (plan M2, slim)."""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from paymentsapp.api.deps import CurrentCustomer, DbSession
from paymentsapp.modules.identity.schemas import KycOut
from paymentsapp.modules.kyc import service
from paymentsapp.modules.kyc.models import KycProfile

router = APIRouter(prefix="/v1/kyc", tags=["customer"])


class MinimumKyc(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=2, max_length=100)
    date_of_birth: date


def _out(profile: KycProfile) -> KycOut:
    return KycOut(tier=profile.tier, full_name=profile.full_name, date_of_birth=profile.date_of_birth)


@router.get("", response_model=KycOut)
async def status(customer: CurrentCustomer, session: DbSession) -> KycOut:
    profile = await service.get_profile(session, customer.customer.id)
    await session.commit()
    return _out(profile)


@router.post("/minimum", response_model=KycOut)
async def minimum(body: MinimumKyc, customer: CurrentCustomer, session: DbSession) -> KycOut:
    return _out(
        await service.submit_minimum_kyc(
            session, customer.customer.id, customer.actor, body.full_name, body.date_of_birth
        )
    )


@router.post("/full", response_model=KycOut)
async def full(customer: CurrentCustomer, session: DbSession) -> KycOut:
    return _out(await service.upgrade_to_full_kyc(session, customer.customer.id, customer.actor))
