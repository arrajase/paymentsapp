"""Admin API: ledger totals and reconciliation with the bank (plan M4, M16)."""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from paymentsapp.api.deps import CurrentAdmin, DbSession, admin_ip_allowed, require_roles
from paymentsapp.modules.admin.service import AdminContext, AdminRole
from paymentsapp.modules.compliance.limits import IST
from paymentsapp.modules.wallet import reconciliation
from paymentsapp.modules.wallet.models import ReconciliationRun

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(admin_ip_allowed)], tags=["admin"])

Reconciler = Annotated[
    AdminContext,
    Depends(require_roles(AdminRole.OPERATIONS, AdminRole.PLATFORM_ADMIN, AdminRole.COMPLIANCE)),
]


class RunOut(BaseModel):
    id: int
    provider: str
    business_date: date
    ran_at: datetime
    ran_by: str
    bank_balance: int | None
    ledger_balance: int
    matched: int
    clean: bool
    issues: list[dict[str, Any]]


class TotalsOut(BaseModel):
    escrow: int  # paise
    in_transit: int
    wallets: int


class ReconciliationOut(BaseModel):
    totals: TotalsOut
    runs: list[RunOut]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date | None = None  # defaults to today in IST


def _run_out(run: ReconciliationRun) -> RunOut:
    return RunOut(
        id=run.id,
        provider=run.provider,
        business_date=run.business_date,
        ran_at=run.ran_at,
        ran_by=run.ran_by,
        bank_balance=run.bank_balance,
        ledger_balance=run.ledger_balance,
        matched=run.matched,
        clean=run.clean,
        issues=run.issues,
    )


@router.get("/reconciliation", response_model=ReconciliationOut)
async def overview(_: CurrentAdmin, session: DbSession) -> ReconciliationOut:
    sums = await reconciliation.totals(session)
    runs = await reconciliation.latest_runs(session)
    return ReconciliationOut(
        totals=TotalsOut(escrow=sums.escrow, in_transit=sums.in_transit, wallets=sums.wallets),
        runs=[_run_out(r) for r in runs],
    )


@router.post("/reconciliation/run", response_model=RunOut, status_code=201)
async def run(body: RunRequest, admin: Reconciler, session: DbSession) -> RunOut:
    """Reconcile now. Reads the bank's escrow balance and statement, so it can take a while if the bank is asleep."""
    business_date = body.business_date or datetime.now(IST).date()
    return _run_out(await reconciliation.reconcile(session, business_date, str(admin.user.id)))
