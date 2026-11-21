# paymentsapp

A wallet app for customers in India (React Native mobile app), an admin website for internal staff (Next.js), and
one FastAPI backend. Development and demos run against the mock bank,
[mockbankforpayments](https://github.com/arrajase/mockbankforpayments), which also holds the wallet's escrow
account.

- [PROJECT_PLAN.md](PROJECT_PLAN.md): the design, modules, security and regulatory checks, and roadmap.
- [build-and-compliance-roadmap.md](build-and-compliance-roadmap.md): the parallel build and regulatory tracks.
- [docs/integrations/mockbank-api.md](docs/integrations/mockbank-api.md): how the backend uses the mock bank.

## Quick start

After the one-time setup below (backend `.env`, database, admin accounts), start everything with:

```
dev.cmd                       # phone with Expo Go on the same Wi-Fi (default)
dev.cmd -Device emulator      # Android emulator instead (start it first)
dev.cmd -Skip worker,admin    # leave some parts out
```

It first opens the hosted mock bank's website in your browser, so the bank (which sleeps when idle) wakes up while
the rest starts; that page is also where test customers approve requests with their UPI PIN (`-Skip bank` to leave
it out). Then it opens one Windows Terminal window with a tab each for the backend, the worker, the admin website and
Expo, and points the mobile app at this PC (`mobile/.env.local`). Scan the QR code in the Expo tab with Expo Go. Sign-in codes
are in the admin website's Dev inbox (http://localhost:3000). Close the window to stop everything. `dev.cmd -DryRun`
shows what it would start.

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

To reach the backend from a phone on the same Wi-Fi, add `--host 0.0.0.0` and set `EXPO_PUBLIC_API_URL` in
`mobile/.env.local` to `http://<this PC's IP>:8001`.

The worker runs next to the API, in its own terminal. It picks up bank events the webhook missed (the hosted mock
bank can't reach a laptop):

```
uv run python -m paymentsapp.worker
```

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
**Plugins** (settings, secrets shown as previews, propose a change, health with a **Check now** button), **Controls** (security controls and feature
switches, within the limits in `config/security_controls.yaml` and `config/features.yaml`), **Approvals** (approve,
reject or withdraw) and **Audit log**. A change takes effect only after a different admin with the `security_approver` role approves it.

The first time, import the mock bank values from `backend/.env` into the plugin registry:

```
cd backend
uv run python -m paymentsapp.cli seed-plugins
```

After that, change them on the admin website rather than in `.env`.

## Mobile app

Expo (React Native), running in Expo Go on the Android emulator. Start the backend first (port 8001), then:

```
cd mobile
npm install
cp .env.example .env.local        # EXPO_PUBLIC_API_URL=http://10.0.2.2:8001 (the emulator's name for this PC)
npx expo start --android          # installs Expo Go on the emulator the first time, then opens the app
```

Sign up with a test number such as `+91 90000 00001`. The code arrives as an SMS in the emulator (when `adb` is on
PATH or `ADB_PATH` is set in `backend/.env`) and on the admin website's **Dev inbox**. To try fingerprint unlock, add
a fingerprint in the emulator's Android settings, switch it on in the app, then touch the sensor from the emulator's
extended controls (or run `adb -e emu finger touch 1`).

Checks: `npm run typecheck` and `npm run lint`.

## Deploying to Render (demo)

`render.yaml` describes two free Render web services: **paymentsapp-api** (the backend, with the worker running
inside it, as Render's free tier has no background workers) and **paymentsapp-admin** (the admin website). The
mobile app is published with **EAS Update**, so Expo Go on any phone opens it without this PC running.

This is a demo deployment: `ENVIRONMENT=development` keeps the Dev inbox (sign-in codes are read on the admin
website) and password-only admin sign-in, on a public address. Use mock money and test identities only.

1. Commit and push to GitHub.
2. **Neon:** create a branch (for example `render`) from the database the laptop uses, and copy its connection
   string. The branch is a copy of today's data, and from then on the laptop and Render don't affect each other.
3. **Render:** New > Blueprint > this repository. When asked, paste:
   - `DATABASE_URL`: the branch's connection string;
   - `FIELD_ENCRYPTION_KEYS` and `BLIND_INDEX_KEY`: the same values as in `backend/.env` (the copied data is
     encrypted with them);
   - `ADMIN_API_BASE_URL`: leave it for now (Render may ask; put any `https://` address and fix it in step 5).
4. Wait for the first deploy, then open `https://<api>.onrender.com/health`.
5. On **paymentsapp-admin** > Environment, set `ADMIN_API_BASE_URL` to the API's address
   (`https://<api>.onrender.com`) and save; it redeploys.
6. **Mock bank:** on its Developer page, set the app's webhook URL to
   `https://<api>.onrender.com/v1/webhooks/mockbank`.
7. **Mobile app** (once): create a free account at expo.dev, then in `mobile/` run `npx eas-cli login`,
   `npx eas-cli init` and `npx eas-cli update:configure` (keep `runtimeVersion` as `{"policy": "sdkVersion"}`, which
   Expo Go needs). Then publish from the repo root:

   ```
   publish-app.cmd -ApiUrl https://<api>.onrender.com
   ```

   On the phone, sign in to Expo Go with the same Expo account and open **paymentsapp**. After any change to the app,
   run `publish-app.cmd` again (it remembers the address in `mobile/.env.render`).

To change an admin's password on the deployment, run from `backend/` with `DATABASE_URL` set to the branch:
`uv run python -m paymentsapp.cli reset-admin-password --login-id <login>`.

Free services sleep after 15 minutes without traffic; the first request then takes about a minute (the mock bank
sleeps too). While the API sleeps the worker is paused, and missed bank events are caught up when it wakes.

## Status

Phase 0 (foundations) is in progress. Done: backend skeleton, logging with PII redaction, the error format, the
mock bank adapter, the reviewed config files, the database layer with migrations, the hash-chained audit log, field
encryption, admin sign-in with roles, the plugin registry with maker-checker approvals, and the admin website
(sign-in, plugins, controls, approvals, audit log, Dev inbox), and runtime controls with `GET /v1/app-config` for the
mobile app.

Phase 1 (customer onboarding) backend is done: sign-up and sign-in by OTP, device registration with an app PIN,
device-signed returning sign-in, rotating refresh tokens, a second identifier, slim KYC (T0, T1, mock T2) and consents.
Try it from http://localhost:8001/docs: request a code, read it on the admin website's **Dev inbox**, then verify it.
The mobile app covers the same flows on the Android emulator.

Phase 2 (UPI linking) is built: step-up confirmation with the app PIN and device key, finding the customer's UPI ID
by their verified mobile at the mock bank, the KYC name match, a `LINK` consent approved with the UPI PIN on the mock
bank website, set default, unlink, and the signed bank webhook. To try it in the app, complete minimum KYC with the
same name as the mock bank account (for the test customer `test1`, **Test User**), open **Bank accounts (UPI)**, link
the UPI ID found for your mobile, then approve the request under **App Permissions** on the mock bank website and
tap **I've approved it**. The worker polls the bank's event feed for missed webhooks, the
admin website shows each plugin's health, and the contract tests run nightly in CI
(`.github/workflows/mockbank-contract.yml`, which needs the `MOCKBANK_*` repository secrets). Refusing to disable a
plugin that holds money arrives with the ledger in Phase 3.

Phase 3 (wallet and payments) is built. In the app's **Wallet**:
- **Add money** from your linked UPI ID; approve it under **Pending Requests** on the mock bank website with the UPI
  PIN.
- **Pay a UPI ID**, for example a merchant created as an ordinary customer on the mock bank website.
- **Send to a wallet** (another app customer, by mobile number) and **Withdraw to bank**: both need full KYC, and
  for sending, the recipient needs full KYC too.

Payments out are confirmed with the app PIN. Limits by KYC tier are in `config/compliance_limits.yaml`. The admin
website's **Reconciliation** page shows the ledger totals and compares the escrow account with the bank. The bank's
escrow balance must equal the ledger's for a clean run, so money put into escrow outside the app (for example a test
deposit on the mock bank website) shows up as an issue.

Integration tests wipe and rebuild the `*_test` database on every run. CI has no database, so it runs unit tests
only.
