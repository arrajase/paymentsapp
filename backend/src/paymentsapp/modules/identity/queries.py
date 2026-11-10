"""Read-only questions other modules may ask about a customer's identity, without touching identity's tables."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.crypto import get_cipher
from paymentsapp.modules.identity.models import CustomerIdentifier


def identifier_context(identifier_id: uuid.UUID) -> str:
    return f"customer_identifiers.value:{identifier_id}"


async def has_verified_identifier(session: AsyncSession, customer_id: uuid.UUID, kind: str) -> bool:
    found = await session.scalar(
        select(CustomerIdentifier.id).where(
            CustomerIdentifier.customer_id == customer_id, CustomerIdentifier.kind == kind
        )
    )
    return found is not None


async def verified_mobile(session: AsyncSession, customer_id: uuid.UUID) -> str | None:
    """The customer's verified mobile number (+91 and 10 digits), decrypted, or None."""
    row = await session.scalar(
        select(CustomerIdentifier).where(
            CustomerIdentifier.customer_id == customer_id, CustomerIdentifier.kind == "mobile"
        )
    )
    return get_cipher().decrypt(row.value_encrypted, identifier_context(row.id)) if row else None


async def contact_points(session: AsyncSession, customer_id: uuid.UUID) -> list[tuple[str, str]]:
    """(channel, destination) for every verified identifier, decrypted, for sending alerts."""
    rows = (
        await session.scalars(select(CustomerIdentifier).where(CustomerIdentifier.customer_id == customer_id))
    ).all()
    cipher = get_cipher()
    return [(r.kind, cipher.decrypt(r.value_encrypted, identifier_context(r.id))) for r in rows]
