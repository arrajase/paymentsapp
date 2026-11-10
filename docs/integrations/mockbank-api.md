# Mock bank integration

How the backend uses the mock bank. The mock bank's
[README](https://github.com/arrajase/mockbankforpayments#integrating-a-payments-app-the-gateway) is the reference for
request and response fields; PROJECT_PLAN.md section 9 explains the flows. This file records what the adapter
(`backend/src/paymentsapp/integrations/mockbank/`) actually does.

Contract version: mock bank commit `aa36b85` (customer mobile numbers and `POST /upi/lookup`), November 2026.

## One-time setup per environment

1. On the mock bank website, sign up a business customer, for example "PaymentsApp".
2. Give it two INR accounts, Escrow and Operating, each with its own UPI ID (for example
   `paymentsappescrow@okmockbank` and `paymentsappops@okmockbank`).
3. Issue an API client owned by that customer: on the mock bank website's **Developer** page, signed in as that
   customer, or with the mock bank's CLI on the machine or service running it:
   ```
   python -m app.mockbank.cli create-client --name paymentsapp-dev --owner <customer_id> \
       --webhook-url https://<backend-host>/v1/webhooks/mockbank
   ```
   The API key and webhook secret are shown once. Put them in `backend/.env` as `MOCKBANK_API_KEY` and
   `MOCKBANK_WEBHOOK_SECRET`, with the two UPI IDs as `MOCKBANK_ESCROW_UPI_ID` and `MOCKBANK_OPERATING_UPI_ID`, then
   run `cli seed-plugins` once. After that the admin website's plugin settings are the source of truth: the backend
   builds the adapter from them, not from `.env`.
4. For UPI linking, test customers add a mobile number on their mock bank profile and link it to their UPI ID
   (**Edit UPI ID**). The account-holder name must match the name they give in the app's minimum KYC.
5. For contract tests, also sign up an ordinary test customer with an INR account and a UPI ID, and set
   `MOCKBANK_TEST_CUSTOMER_UPI_ID`.

Use a separate API client and escrow account for each environment.

## Adapter behaviour

| Situation | What the adapter does |
|---|---|
| Error with `retryable: true`, timeout, connection failure, non-JSON 5xx | Retries up to 3 attempts with exponential backoff and jitter; honours `Retry-After` (capped at 30 s) |
| Error with `retryable: false` | Raises `BankError` with the bank's code at once |
| Money call (`pay`, `create_collect`) | Always sends the caller's `Idempotency-Key`; every retry resends the same key and body |
| Money call still unresolved after the last attempt, or an unreadable success reply | Raises `OutcomeUnknown`. The payment must be marked `UNKNOWN`, then settled with `find_transaction(client_reference)` or by repeating the request with the same key |
| Read still failing after the last attempt | Raises `BankUnavailable` |
| 5 consecutive failures | Circuit breaker opens for 30 s and calls fail fast with `BankUnavailable` (nothing is sent, so even a money call's outcome is known: not done) |
| `422 INSUFFICIENT_FUNDS` on `pay` | Returns the bank's recorded `FAILED` transaction instead of raising |
| `404 TRANSACTION_NOT_FOUND` on lookup by reference | `find_transaction` returns `None`: the bank never recorded it |
| `422 VALIDATION_ERROR` on `POST /upi/lookup` | `lookup_mobile` returns `None`: no UPI ID is linked to that number. The adapter sends the 10 digits without `+91` |
| `verify` and `lookup` limit (10 a minute per client) | `429 RATE_LIMITED` is retried after `Retry-After`; the app's own per-customer limit (`vpa_verifications_per_hour`) applies first |
| Webhook | Signature checked on the raw bytes (HMAC-SHA256 of `t.body`), timestamp within 5 minutes, event ID header must match the body. Any failure raises `WebhookVerificationError` (the endpoint answers 401). `POST /v1/webhooks/mockbank` de-duplicates on `event_id`; for `consent.updated` it re-reads the consent from the bank before changing a link, and answers 503 if the bank can't be reached, so the event is delivered again |
| Cold start (free-tier hosting) | `warm_up()` waits up to 90 s on `/health` (measured: about 43 s on 20 October 2026) |

The adapter sends `currency: INR` on every money call. Amounts are integer paise (`amount_cents` on the wire).

## Sandbox triggers

With `SANDBOX_TRIGGERS=true` on the mock bank, the last two digits of the amount in paise force a failure:
`13` money moves then a 30 s delay, `14` money moves then `500`, `15` `503` with nothing moved, `16` the collect
request expires at once. Use these for the failure-path tests in PROJECT_PLAN.md section 11.
