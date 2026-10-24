# Build + Compliance Roadmap (two parallel tracks)

**Repo:** https://github.com/arrajase/paymentsapp  
**Mobile framework:** not decided yet (backend and web first).

*Written 2026-09-27. Assumes the non-custodial model: the app never holds user money; savings go by UPI Autopay mandate into a regulated product in the user's name.*

## Principles

- **Mock the providers behind interfaces.** Every external dependency sits behind an adapter interface: `AAProvider`, `PaymentProvider`, `MandateProvider`, `InvestmentProvider`. Build against mocks now, then swap in partner sandboxes and later production as the agreements land.
- **Build compliance into the design.** Consent records, an audit log, an AI decision log, encryption, data retention rules and 24-hour pre-debit notices are features from day one, not add-ons.
- **Keep a regulation → control → test matrix.** Each regulatory requirement becomes a control, a ticket and an automated test.
- **Partner first, licence later.** Use regulated partners (a TSP/FIU, a payment gateway, an MF platform). Get your own licences only once there is traction.
- **Gate on real money and real data.** No real user money or data until: the company is incorporated, a legal opinion is in hand, partner agreements are signed, the privacy notice and consent flows are live, and VAPT is passed.

## Track A: Build

| Weeks | Deliverable |
|---|---|
| 1–2 | Monorepo, FastAPI skeleton, Postgres, docker-compose, CI, adapter interfaces with mocks, auth, audit log |
| 3–6 | Double-entry ledger; statement ingestion and normalisation; synthetic statement generator; classifier v1 (rules) and v2 (ML/LLM with confidence and reasons) |
| 7–10 | Savings rules engine; round-up accrual; mandate simulator (AFA, max amount, 24-hour pre-debit notice, pause/cancel); simulated MF allotment; reconciliation job and alerts |
| 11–14 | Web (Next.js) UI, plus mobile once the framework is chosen: onboarding, consent screens, rules, dashboard, explanations of AI decisions |
| 15+ | Swap mocks for partner sandboxes (AA sandbox, payment-gateway UPI and Autopay sandbox, MF sandbox); end-to-end tests |
| After gate | Closed beta with friends and family, small caps |

## Track B: Regulatory and business

| Weeks | Deliverable |
|---|---|
| 1–4 | Lock the non-custodial model; incorporate a Pvt Ltd; engage a fintech lawyer for a regulatory opinion memo; draft privacy notice and consent language (DPDP) |
| 3–8 | Partner shortlist and talks: AA/TSP that provides FIU cover; payment gateway with UPI intent and UPI Autopay; MF transaction platform/distributor. Start the NISM V-A exam and AMFI ARN application |
| 6–12 | Sign partner agreements; DPDP artefacts (DPIA, retention schedule, grievance officer, breach runbook for the 6-hour CERT-In rule); information-security policies; India-region hosting |
| 12–16 | VAPT by a CERT-In empanelled auditor; partner go-live audits; marketing and claims review (no guaranteed returns) |
| Later | Own FIU route via RIA, and TPAP, only if scale justifies it |

## Where the tracks depend on each other

- Partner sandbox credentials (Track B, weeks 3–8) are needed to swap out mocks (Track A, week 15+).
- The lawyer's consent wording (Track B) is needed for the consent UI (Track A, weeks 11–14).
- VAPT (Track B) needs a feature-complete build (Track A).
- The pilot needs everything above.
