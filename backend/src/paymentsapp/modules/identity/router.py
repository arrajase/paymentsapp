"""Customer API: sign-up, sign-in, devices and profile (plan M1). Every route under /v1."""

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from paymentsapp.api.deps import CurrentCustomer, DbSession, client_ip
from paymentsapp.core.errors import AppError
from paymentsapp.modules.consent import service as consent
from paymentsapp.modules.identity import otp, service, sessions, step_up
from paymentsapp.modules.identity.devices import sign_in_message, step_up_message
from paymentsapp.modules.identity.models import CustomerIdentifier
from paymentsapp.modules.identity.schemas import (
    AddIdentifierVerify,
    ChallengeOut,
    ChallengeRequest,
    CodeVerifiedOut,
    DeviceEnrol,
    DeviceOut,
    DeviceSignIn,
    EnrolledOut,
    IdentifierOut,
    KycOut,
    MeOut,
    NoticeOut,
    OtpRequest,
    OtpRequested,
    OtpVerify,
    RefreshRequest,
    StepUpChallengeRequest,
    StepUpOut,
    StepUpRequest,
    TokensOut,
)
from paymentsapp.modules.kyc.service import get_profile

router = APIRouter(prefix="/v1", tags=["customer"])


def _tokens(pair: sessions.TokenPair) -> TokensOut:
    return TokensOut(
        access_token=pair.access_token,
        access_expires_at=pair.access_expires_at,
        refresh_token=pair.refresh_token,
        refresh_expires_at=pair.refresh_expires_at,
    )


@router.post("/auth/otp/request", response_model=OtpRequested, status_code=status.HTTP_202_ACCEPTED)
async def request_code(body: OtpRequest, request: Request, session: DbSession) -> OtpRequested:
    """Send a sign-in code. The answer is the same whether or not an account exists."""
    sent = await otp.request_otp(session, body.kind, body.identifier, ip=client_ip(request))
    return OtpRequested(
        challenge_id=sent.challenge_id,
        sent_to=sent.masked,
        expires_at=sent.expires_at,
        resend_after_seconds=sent.resend_after_seconds,
    )


@router.post("/auth/otp/verify", response_model=CodeVerifiedOut)
async def verify_code(body: OtpVerify, session: DbSession) -> CodeVerifiedOut:
    verified = await service.verify_sign_in_code(session, body.challenge_id, body.code)
    terms = consent.notices()[consent.TERMS]
    return CodeVerifiedOut(
        enrolment_token=verified.enrolment_token,
        expires_at=verified.expires_at,
        next_step=verified.next_step,
        terms=NoticeOut(**terms.__dict__) if verified.next_step == "create_account" else None,
    )


@router.post("/auth/devices", response_model=EnrolledOut, status_code=status.HTTP_201_CREATED)
async def enrol_device(body: DeviceEnrol, request: Request, session: DbSession) -> EnrolledOut:
    enrolled = await service.enrol_device(
        session,
        enrolment_token=body.enrolment_token,
        public_key=body.public_key,
        platform=body.platform,
        hardware_backed=body.hardware_backed,
        label=body.label,
        pin=body.pin,
        accepted_terms_version=body.accepted_terms_version,
        ip=client_ip(request),
    )
    return EnrolledOut(
        customer_id=enrolled.customer_id,
        device_id=enrolled.device_id,
        new_customer=enrolled.new_customer,
        replaced_device=enrolled.replaced_device,
        tokens=_tokens(enrolled.tokens),
    )


@router.post("/auth/challenge", response_model=ChallengeOut)
async def challenge(body: ChallengeRequest, session: DbSession) -> ChallengeOut:
    issued = await service.create_challenge(session, body.device_id)
    return ChallengeOut(
        challenge_id=issued.challenge_id,
        nonce=issued.nonce,
        expires_at=issued.expires_at,
        sign=sign_in_message(str(issued.challenge_id), issued.nonce).decode(),
    )


@router.post("/auth/sign-in", response_model=TokensOut)
async def sign_in(body: DeviceSignIn, request: Request, session: DbSession) -> TokensOut:
    pair = await service.sign_in_with_device(
        session,
        device_id=body.device_id,
        challenge_id=body.challenge_id,
        signature=body.signature,
        pin=body.pin,
        biometric=body.biometric,
        ip=client_ip(request),
    )
    return _tokens(pair)


@router.post("/auth/step-up/challenge", response_model=ChallengeOut)
async def step_up_challenge(
    body: StepUpChallengeRequest, customer: CurrentCustomer, session: DbSession
) -> ChallengeOut:
    """Start confirming a sensitive action: sign `sign` with the device key, then call POST /v1/auth/step-up."""
    issued = await step_up.create_step_up_challenge(session, customer, body.action)
    return ChallengeOut(
        challenge_id=issued.challenge_id,
        nonce=issued.nonce,
        expires_at=issued.expires_at,
        sign=step_up_message(str(issued.challenge_id), issued.nonce, body.action).decode(),
    )


@router.post("/auth/step-up", response_model=StepUpOut)
async def confirm_step_up(body: StepUpRequest, customer: CurrentCustomer, session: DbSession) -> StepUpOut:
    """A single-use token for the action, sent as X-Step-Up-Token to that action's endpoint."""
    done = await step_up.verify_step_up(
        session,
        customer,
        challenge_id=body.challenge_id,
        signature=body.signature,
        pin=body.pin,
        biometric=body.biometric,
    )
    return StepUpOut(step_up_token=done.token, action=done.action, expires_at=done.expires_at)


@router.post("/auth/token/refresh", response_model=TokensOut)
async def refresh(body: RefreshRequest, request: Request, session: DbSession) -> TokensOut:
    return _tokens(await sessions.refresh(session, body.refresh_token, body.device_id, client_ip(request)))


@router.post("/auth/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(customer: CurrentCustomer, session: DbSession) -> None:
    await sessions.sign_out(session, customer)


@router.get("/me", response_model=MeOut)
async def me(customer: CurrentCustomer, session: DbSession) -> MeOut:
    identifiers = (
        await session.scalars(
            select(CustomerIdentifier)
            .where(CustomerIdentifier.customer_id == customer.customer.id)
            .order_by(CustomerIdentifier.created_at)
        )
    ).all()
    profile = await get_profile(session, customer.customer.id)
    await session.commit()
    device = customer.device
    return MeOut(
        customer_id=customer.customer.id,
        identifiers=[IdentifierOut(kind=i.kind, masked=i.masked, verified_at=i.verified_at) for i in identifiers],
        device=DeviceOut(
            id=device.id,
            platform=device.platform,
            label=device.label,
            hardware_backed=device.hardware_backed,
            cooling_until=device.cooling_until,
        ),
        kyc=KycOut(tier=profile.tier, full_name=profile.full_name, date_of_birth=profile.date_of_birth),
        pin_set=customer.customer.pin_hash is not None,
    )


@router.post("/me/identifiers/otp", response_model=OtpRequested, status_code=status.HTTP_202_ACCEPTED)
async def add_identifier_code(
    body: OtpRequest, request: Request, customer: CurrentCustomer, session: DbSession
) -> OtpRequested:
    """Verify a second identifier, for example a mobile number for a customer who signed up by email."""
    already = await session.scalar(
        select(CustomerIdentifier.id).where(
            CustomerIdentifier.customer_id == customer.customer.id, CustomerIdentifier.kind == body.kind
        )
    )
    if already:
        raise AppError("IDENTIFIER_EXISTS", f"you already have a verified {body.kind}", status_code=409)
    sent = await otp.request_otp(
        session,
        body.kind,
        body.identifier,
        purpose="add_identifier",
        customer_id=customer.customer.id,
        ip=client_ip(request),
    )
    return OtpRequested(
        challenge_id=sent.challenge_id,
        sent_to=sent.masked,
        expires_at=sent.expires_at,
        resend_after_seconds=sent.resend_after_seconds,
    )


@router.post("/me/identifiers/verify", response_model=IdentifierOut, status_code=status.HTTP_201_CREATED)
async def add_identifier_verify(
    body: AddIdentifierVerify, customer: CurrentCustomer, session: DbSession
) -> IdentifierOut:
    identifier = await service.add_identifier(
        session, customer.customer.id, customer.actor, body.challenge_id, body.code
    )
    return IdentifierOut(kind=identifier.kind, masked=identifier.masked, verified_at=identifier.verified_at)
