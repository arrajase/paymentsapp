"""Imports every module's models so their tables are registered on Base.metadata (used by Alembic).

Add a line here when a module gets its first table.
"""

from paymentsapp.modules.admin import models as admin_models  # noqa: F401
from paymentsapp.modules.audit import models as audit_models  # noqa: F401
from paymentsapp.modules.plugins import models as plugin_models  # noqa: F401
