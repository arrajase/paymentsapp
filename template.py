"""Scaffold the paymentsapp folder structure described in PROJECT_PLAN.md.

Usage:
    npx create-expo-app@latest mobile --template blank-typescript   # optional, run first
    npx create-next-app@latest admin-web --ts --app --src-dir --tailwind --eslint  # optional, run first
    python template.py

Safe to re-run: existing non-empty files are never overwritten.
"""
import os
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='[%(asctime)s]: %(message)s:')


project_name = "paymentsapp"
backend_pkg = f"backend/src/{project_name}"
mobile_src = "mobile/src"
admin_src = "admin-web/src"

backend_modules = [
    "identity",
    "kyc",
    "upi",
    "wallet",
    "payments",
    "risk",
    "compliance",
    "consent",
    "statements",
    "insights",
    "deals",
    "notifications",
    "grievance",
    "audit",
    "admin",
    "plugins",
    "controls",
]
module_files = ["__init__.py", "router.py", "schemas.py", "service.py", "models.py", "repository.py"]

mobile_features = ["auth", "onboarding", "upi", "wallet", "payments", "insights", "deals", "support", "profile"]

list_of_files = [
    # ---- Repository root ----
    ".github/workflows/.gitkeep",
    ".gitignore",
    "README.md",
    "docker-compose.yml",
    "data/.gitkeep",
    "research/spend_insights.ipynb",
    "research/deals_research.ipynb",

    # ---- Docs ----
    "docs/architecture.md",
    "docs/security/threat-model.md",
    "docs/security/mobile-security-checklist.md",
    "docs/security/incident-response.md",
    "docs/compliance/regulatory-matrix.md",
    "docs/compliance/data-retention-policy.md",
    "docs/compliance/ai-governance.md",
    "docs/integrations/mockbank-api.md",

    # ---- Config (non-secret, versioned) ----
    "config/params.yaml",
    "config/schema.yaml",
    "config/compliance_limits.yaml",
    "config/risk_rules.yaml",
    "config/aml_scenarios.yaml",
    "config/providers.yaml",
    "config/security_controls.yaml",

    # ---- Backend ----
    "backend/Dockerfile",
    "backend/pyproject.toml",
    "backend/.env.example",
    f"{backend_pkg}/__init__.py",
    f"{backend_pkg}/main.py",

    f"{backend_pkg}/api/__init__.py",
    f"{backend_pkg}/api/deps.py",
    f"{backend_pkg}/api/middleware.py",
    f"{backend_pkg}/api/v1/__init__.py",
    f"{backend_pkg}/api/v1/router.py",
    f"{backend_pkg}/api/v1/webhooks.py",
    f"{backend_pkg}/api/admin/__init__.py",
    f"{backend_pkg}/api/admin/router.py",

    f"{backend_pkg}/core/__init__.py",
    f"{backend_pkg}/core/config.py",
    f"{backend_pkg}/core/security.py",
    f"{backend_pkg}/core/crypto.py",
    f"{backend_pkg}/core/logging.py",
    f"{backend_pkg}/core/errors.py",
    f"{backend_pkg}/core/idempotency.py",
    f"{backend_pkg}/core/rate_limit.py",

    f"{backend_pkg}/db/__init__.py",
    f"{backend_pkg}/db/base.py",
    f"{backend_pkg}/db/session.py",

    f"{backend_pkg}/constants/__init__.py",
    f"{backend_pkg}/constants/enums.py",
    f"{backend_pkg}/constants/error_codes.py",

    # Module-specific files (standard module files are added below)
    f"{backend_pkg}/modules/__init__.py",
    f"{backend_pkg}/modules/identity/otp.py",
    f"{backend_pkg}/modules/identity/devices.py",
    f"{backend_pkg}/modules/identity/sessions.py",
    f"{backend_pkg}/modules/kyc/providers.py",
    f"{backend_pkg}/modules/upi/name_match.py",
    f"{backend_pkg}/modules/wallet/ledger.py",
    f"{backend_pkg}/modules/wallet/reconciliation.py",
    f"{backend_pkg}/modules/payments/state_machine.py",
    f"{backend_pkg}/modules/payments/outbox.py",
    f"{backend_pkg}/modules/risk/rules_engine.py",
    f"{backend_pkg}/modules/risk/device_signals.py",
    f"{backend_pkg}/modules/compliance/limits.py",
    f"{backend_pkg}/modules/compliance/screening.py",
    f"{backend_pkg}/modules/compliance/monitoring.py",
    f"{backend_pkg}/modules/compliance/cases.py",
    f"{backend_pkg}/modules/compliance/reporting.py",
    f"{backend_pkg}/modules/compliance/retention.py",
    f"{backend_pkg}/modules/statements/normalizer.py",
    f"{backend_pkg}/modules/statements/categorizer.py",
    f"{backend_pkg}/modules/audit/hash_chain.py",
    f"{backend_pkg}/modules/plugins/change_requests.py",
    f"{backend_pkg}/modules/plugins/health.py",
    f"{backend_pkg}/modules/controls/bounds.py",
    f"{backend_pkg}/modules/controls/snapshot.py",

    # Integrations
    f"{backend_pkg}/integrations/__init__.py",
    f"{backend_pkg}/integrations/bank_gateway.py",
    f"{backend_pkg}/integrations/registry.py",
    f"{backend_pkg}/integrations/mockbank/__init__.py",
    f"{backend_pkg}/integrations/mockbank/client.py",
    f"{backend_pkg}/integrations/mockbank/schemas.py",
    f"{backend_pkg}/integrations/mockbank/signing.py",
    f"{backend_pkg}/integrations/mockbank/webhooks.py",
    f"{backend_pkg}/integrations/mockbank/errors.py",
    f"{backend_pkg}/integrations/messaging/__init__.py",
    f"{backend_pkg}/integrations/messaging/sms.py",
    f"{backend_pkg}/integrations/messaging/email.py",
    f"{backend_pkg}/integrations/messaging/push.py",
    f"{backend_pkg}/integrations/screening_lists/__init__.py",
    f"{backend_pkg}/integrations/screening_lists/provider.py",
    f"{backend_pkg}/integrations/market_data/__init__.py",
    f"{backend_pkg}/integrations/market_data/provider.py",

    # AI layer
    f"{backend_pkg}/ai/__init__.py",
    f"{backend_pkg}/ai/llm/__init__.py",
    f"{backend_pkg}/ai/llm/client.py",
    f"{backend_pkg}/ai/agents/__init__.py",
    f"{backend_pkg}/ai/agents/spend_insights_agent.py",
    f"{backend_pkg}/ai/agents/deals_research_agent.py",
    f"{backend_pkg}/ai/agents/deals_recommender.py",
    f"{backend_pkg}/ai/nodes/__init__.py",
    f"{backend_pkg}/ai/state/__init__.py",
    f"{backend_pkg}/ai/state/insights_state.py",
    f"{backend_pkg}/ai/state/deals_state.py",
    f"{backend_pkg}/ai/tools/__init__.py",
    f"{backend_pkg}/ai/prompts/.gitkeep",
    f"{backend_pkg}/ai/guardrails/__init__.py",
    f"{backend_pkg}/ai/guardrails/pii_redaction.py",
    f"{backend_pkg}/ai/guardrails/output_validation.py",
    f"{backend_pkg}/ai/guardrails/financial_claims.py",
    f"{backend_pkg}/ai/evals/__init__.py",

    # Background workers
    f"{backend_pkg}/workers/__init__.py",
    f"{backend_pkg}/workers/celery_app.py",
    f"{backend_pkg}/workers/tasks/__init__.py",
    f"{backend_pkg}/workers/tasks/payment_status.py",
    f"{backend_pkg}/workers/tasks/reconciliation.py",
    f"{backend_pkg}/workers/tasks/statement_sync.py",
    f"{backend_pkg}/workers/tasks/insights_jobs.py",
    f"{backend_pkg}/workers/tasks/aml_monitoring.py",
    f"{backend_pkg}/workers/tasks/retention.py",
    f"{backend_pkg}/workers/tasks/screening_refresh.py",

    # Tests
    "backend/tests/__init__.py",
    "backend/tests/conftest.py",
    "backend/tests/unit/.gitkeep",
    "backend/tests/integration/.gitkeep",
    "backend/tests/contract/.gitkeep",
    "backend/tests/security/.gitkeep",
    "backend/tests/ai_evals/.gitkeep",
]

for module in backend_modules:
    for file in module_files:
        list_of_files.append(f"{backend_pkg}/modules/{module}/{file}")

# ---- Mobile (React Native + Expo, expo-router) ----
mobile_files = [
    f"{mobile_src}/app/_layout.tsx",
    f"{mobile_src}/app/(auth)/sign-in.tsx",
    f"{mobile_src}/app/(auth)/verify-otp.tsx",
    f"{mobile_src}/app/(onboarding)/kyc.tsx",
    f"{mobile_src}/app/(onboarding)/link-upi.tsx",
    f"{mobile_src}/app/(onboarding)/set-app-pin.tsx",
    f"{mobile_src}/app/(tabs)/_layout.tsx",
    f"{mobile_src}/app/(tabs)/home.tsx",
    f"{mobile_src}/app/(tabs)/wallet.tsx",
    f"{mobile_src}/app/(tabs)/pay.tsx",
    f"{mobile_src}/app/(tabs)/insights.tsx",
    f"{mobile_src}/app/(tabs)/deals.tsx",
    f"{mobile_src}/app/(tabs)/profile.tsx",
    f"{mobile_src}/core/api/client.ts",
    f"{mobile_src}/core/api/idempotency.ts",
    f"{mobile_src}/core/security/device-binding.ts",
    f"{mobile_src}/core/security/device-integrity.ts",
    f"{mobile_src}/core/security/secure-storage.ts",
    f"{mobile_src}/core/security/biometrics.ts",
    f"{mobile_src}/core/security/screen-protection.ts",
    f"{mobile_src}/core/config/env.ts",
    f"{mobile_src}/core/config/app-config.ts",
    f"{mobile_src}/ui/theme.ts",
    f"{mobile_src}/ui/components/.gitkeep",
]

for feature in mobile_features:
    mobile_files += [
        f"{mobile_src}/features/{feature}/api.ts",
        f"{mobile_src}/features/{feature}/hooks.ts",
        f"{mobile_src}/features/{feature}/components/.gitkeep",
    ]

# ---- Admin website (Next.js, App Router) ----
# The admin website is now real code, built by hand; only the folders still to be filled are scaffolded here.
admin_files = [
    f"{admin_src}/app/(console)/controls/.gitkeep",
]

# create-next-app also refuses a non-empty folder
if os.path.exists("admin-web/package.json"):
    list_of_files += admin_files
else:
    logging.warning("Skipping admin-web/: run `npx create-next-app@latest admin-web --typescript --app --src-dir` first, then re-run this script")


for filepath in list_of_files:
    filepath = Path(filepath)
    filedir, filename = os.path.split(filepath)

    if filedir != "":
        os.makedirs(filedir, exist_ok=True)
        logging.info(f"Creating directory:{filedir} for the file {filename}")

    if (not os.path.exists(filepath)) or (os.path.getsize(filepath) == 0):
        with open(filepath, 'w') as f:
            pass
        logging.info(f"Creating empty file: {filepath}")

    else:
        logging.info(f"{filename}  already exists")
