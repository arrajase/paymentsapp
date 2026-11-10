"""Chooses the enabled plugin for each interface (plan 3.1), from the plugin registry the admin website manages."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Environment, get_settings
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.bank_gateway import BankEventSource, PaymentProvider
from paymentsapp.integrations.messaging.base import MessagingProvider, MessagingUnavailable
from paymentsapp.integrations.mockbank.client import MockBankClient
from paymentsapp.modules.plugins.catalogue import get_catalogue
from paymentsapp.modules.plugins.models import PluginSetting
from paymentsapp.modules.plugins.service import read_secret

log = logging.getLogger(__name__)

MOCKBANK = "mockbank"


class ProviderUnavailable(AppError):
    """No usable plugin for an interface: none is enabled, or the enabled one is missing its settings."""

    def __init__(self, interface: str):
        self.interface = interface
        super().__init__("SERVICE_UNAVAILABLE", "this service is temporarily unavailable; try again later", 503)


async def plugin_enabled(session: AsyncSession, key: str) -> bool:
    setting = await session.get(PluginSetting, key)
    return setting is not None and setting.enabled


async def get_messaging(session: AsyncSession) -> MessagingProvider:
    if await plugin_enabled(session, "dev_messages"):
        # Defence in depth: providers.yaml allows this plugin only in development, and so does this check.
        if get_settings().environment != Environment.DEVELOPMENT:
            raise MessagingUnavailable("dev_messages may only run in development")
        from paymentsapp.integrations.devmessages.provider import DevMessagingProvider

        return DevMessagingProvider(session)
    raise MessagingUnavailable("no messaging plugin is enabled")


# ---- Bank ------------------------------------------------------------------------------------------------------

# One client per plugin version, kept between requests so its connection pool and circuit breaker persist. An
# approved change to the plugin (URL, key, secret) bumps the version, and the next request builds a new client.
_bank: tuple[int, MockBankClient] | None = None


async def _bank_client(session: AsyncSession, interface: str) -> MockBankClient:
    global _bank
    setting = await session.get(PluginSetting, MOCKBANK)
    allowed = get_catalogue().allowed.get(interface, {}).get(MOCKBANK, ())
    if setting is None or not setting.enabled or setting.mode not in allowed:
        raise ProviderUnavailable(interface)
    if _bank is not None and _bank[0] == setting.version:
        return _bank[1]

    base_url = setting.config.get("base_url")
    api_key = await read_secret(session, MOCKBANK, "api_key")
    if not base_url or not api_key:
        log.error("mockbank plugin is enabled but has no base_url or api_key", extra={"interface": interface})
        raise ProviderUnavailable(interface)
    client = MockBankClient(base_url, api_key, webhook_secret=await read_secret(session, MOCKBANK, "webhook_secret"))
    # The old client isn't closed here: a request that fetched it a moment ago may still be using it.
    _bank = (setting.version, client)
    return client


async def get_payment_provider(session: AsyncSession) -> PaymentProvider:
    return await _bank_client(session, "payment")


async def get_bank_event_source(session: AsyncSession) -> BankEventSource:
    return await _bank_client(session, "payment")


async def get_bank_website(session: AsyncSession) -> str | None:
    """Where customers approve consents and collect requests: the mock bank's website, which shares its API's host."""
    setting = await session.get(PluginSetting, MOCKBANK)
    return setting.config.get("base_url") if setting and setting.enabled else None


async def close_clients() -> None:
    global _bank
    if _bank is not None:
        await _bank[1].aclose()
        _bank = None
