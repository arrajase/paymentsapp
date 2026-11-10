"""Customer API: UPI linking (plan M3). Changing links needs a step-up token in the X-Step-Up-Token header."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, status

from paymentsapp.api.deps import CurrentCustomer, DbSession
from paymentsapp.integrations.registry import get_bank_website
from paymentsapp.modules.upi import service
from paymentsapp.modules.upi.models import UpiLink
from paymentsapp.modules.upi.name_match import THRESHOLD
from paymentsapp.modules.upi.schemas import CandidateOut, DiscoverOut, LinkCreatedOut, LinkOut, LinkUpdate, UpiIdIn

router = APIRouter(prefix="/v1/upi", tags=["customer"])

StepUpToken = Annotated[str | None, Header(alias="X-Step-Up-Token")]


def _candidate(c: service.Candidate) -> CandidateOut:
    return CandidateOut(
        upi_id=c.upi_id,
        holder_name_masked=c.holder_name_masked,
        name_matches=c.name_score >= THRESHOLD,
        found_by_mobile=c.found_by_mobile,
        can_link=c.can_link,
        problem_code=c.problem[0] if c.problem else None,
        problem=c.problem[1] if c.problem else None,
    )


def _link(link: UpiLink) -> LinkOut:
    return LinkOut(
        id=link.id,
        upi_id=service.link_upi_id(link),
        holder_name_masked=link.holder_name_masked,
        status=link.status,
        status_reason=link.status_reason,
        is_default=link.is_default,
        created_at=link.created_at,
        activated_at=link.activated_at,
        consent_expires_at=link.consent_expires_at,
    )


@router.get("/discover", response_model=DiscoverOut)
async def discover(customer: CurrentCustomer, session: DbSession) -> DiscoverOut:
    """The UPI ID the bank links to your verified mobile number."""
    found = await service.discover(session, customer)
    return DiscoverOut(found=found is not None, candidate=_candidate(found) if found else None)


@router.post("/validate", response_model=CandidateOut)
async def validate(body: UpiIdIn, customer: CurrentCustomer, session: DbSession) -> CandidateOut:
    return _candidate(await service.validate(session, customer, body.upi_id))


@router.get("/links", response_model=list[LinkOut])
async def list_links(customer: CurrentCustomer, session: DbSession) -> list[LinkOut]:
    return [_link(link) for link in await service.list_links(session, customer)]


@router.post("/links", response_model=LinkCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_link(
    body: UpiIdIn, customer: CurrentCustomer, session: DbSession, step_up: StepUpToken = None
) -> LinkCreatedOut:
    link = await service.create_link(session, customer, body.upi_id, step_up)
    return LinkCreatedOut(**_link(link).model_dump(), approve_at=await get_bank_website(session))


@router.patch("/links/{link_id}", response_model=LinkOut)
async def update_link(
    link_id: uuid.UUID, body: LinkUpdate, customer: CurrentCustomer, session: DbSession, step_up: StepUpToken = None
) -> LinkOut:
    return _link(await service.set_default(session, customer, link_id, step_up))


@router.delete("/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_link(
    link_id: uuid.UUID, customer: CurrentCustomer, session: DbSession, step_up: StepUpToken = None
) -> None:
    await service.remove_link(session, customer, link_id, step_up)
