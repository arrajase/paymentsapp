# paymentsapp

A wallet app for customers in India (React Native mobile app), an admin website for internal staff (Next.js), and
one FastAPI backend. Development and demos run against the mock bank,
[mockbankforpayments](https://github.com/arrajase/mockbankforpayments), which also holds the wallet's escrow
account.

- [PROJECT_PLAN.md](PROJECT_PLAN.md): the design, modules, security and regulatory checks, and roadmap.
- [build-and-compliance-roadmap.md](build-and-compliance-roadmap.md): the parallel build and regulatory tracks.
- [docs/integrations/mockbank-api.md](docs/integrations/mockbank-api.md): how the backend uses the mock bank.

## Backend

Needs [uv](https://docs.astral.sh/uv/). uv installs Python 3.12 itself.

```
cd backend
uv sync                                   # install dependencies from uv.lock
cp .env.example .env                      # then fill in values; .env is never committed
uv run uvicorn paymentsapp.main:app --port 8001 --reload
```

The API runs on port 8001, so it doesn't clash with a local mock bank on port 8000. Interactive docs are at
http://localhost:8001/docs (development only).

### Database

Postgres on Neon. Put `DATABASE_URL` and `TEST_DATABASE_URL` in `backend/.env`, pasted as Neon shows them (direct,
not pooled). The backend uses the asyncpg driver and always verifies TLS.

```
uv run alembic upgrade head                          # apply migrations to DATABASE_URL
uv run alembic revision --autogenerate -m "what"     # new migration after changing models; review it before use
```

Each module's tables live in its own `models.py`; add the module to `src/paymentsapp/db/models.py` so Alembic sees
it.

### Admin accounts

Admins sign in to the admin website with a login ID (a name or an email address), password and a code from an authenticator app (TOTP). Create one
with:

```
uv run python -m paymentsapp.cli create-admin --login-id you@paymentsapp.com --name "Your Name" --roles platform_admin
uv run python -m paymentsapp.cli list-admins
```

It asks for the password and prints the TOTP secret once. Roles: `viewer`, `platform_admin` (proposes plugin and
control changes), `security_approver` (approves them), `operations`, `compliance`, `support`. A change needs two
different admins, so create at least one `platform_admin` and one `security_approver`.

### Checks

```
uv run pytest                 # unit tests, plus integration tests if TEST_DATABASE_URL is set
uv run pytest -m contract     # contract tests against the real mock bank (needs MOCKBANK_* in .env)
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
```

CI runs the same checks plus a dependency vulnerability scan (`.github/workflows/backend.yml`).

## Admin website

Needs Node.js 20 or later. Start the backend first (port 8001), then:

```
cd admin-web
npm install
cp .env.example .env.local        # ADMIN_API_BASE_URL=http://localhost:8001
npm run dev                       # http://localhost:3000
```

Sign in with an admin account (see *Admin accounts* above) and the code from your authenticator app. Pages:
**Plugins** (settings, secrets shown as previews, propose a change), **Approvals** (approve, reject or withdraw) and
**Audit log**. A change takes effect only after a different admin with the `security_approver` role approves it.

The first time, import the mock bank values from `backend/.env` into the plugin registry:

```
cd backend
uv run python -m paymentsapp.cli seed-plugins
```

After that, change them on the admin website rather than in `.env`.

## Status

Phase 0 (foundations) is in progress. Done: backend skeleton, logging with PII redaction, the error format, the
mock bank adapter, the reviewed config files, the database layer with migrations, the hash-chained audit log, field
encryption, admin sign-in with roles, the plugin registry with maker-checker approvals, and the admin website
(sign-in, plugins, approvals, audit log). Next: runtime controls (feature flags and security controls) on the same
approval workflow, then the mobile app shell.

Integration tests wipe and rebuild the `*_test` database on every run. CI has no database, so it runs unit tests
only.
