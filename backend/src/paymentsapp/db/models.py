"""Imports every module's models so their tables are registered on Base.metadata (used by Alembic).

Add a line here when a module gets its first table.
"""

from paymentsapp.integrations.devmessages import models as dev_message_models  # noqa: F401
from paymentsapp.modules.admin import models as admin_models  # noqa: F401
from paymentsapp.modules.audit import models as audit_models  # noqa: F401
from paymentsapp.modules.bankevents import models as bank_event_models  # noqa: F401
from paymentsapp.modules.consent import models as consent_models  # noqa: F401
from paymentsapp.modules.controls import models as control_models  # noqa: F401
from paymentsapp.modules.identity import models as identity_models  # noqa: F401
from paymentsapp.modules.kyc import models as kyc_models  # noqa: F401
from paymentsapp.modules.payments import models as payment_models  # noqa: F401
from paymentsapp.modules.plugins import models as plugin_models  # noqa: F401
from paymentsapp.modules.upi import models as upi_models  # noqa: F401
from paymentsapp.modules.wallet import models as wallet_models  # noqa: F401
