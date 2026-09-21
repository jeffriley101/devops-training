# KWS Production configuration (activation closed by default)

Production KWS support is present in code but is unavailable unless all explicit
Production assertions and credentials below are configured. Merely deploying the
code or adding credentials does not activate it. Test values are never consulted
by the Production path.

| Variable | Requirement |
|---|---|
| `APP_ENV` | Exactly `production`. |
| `KWS_PRODUCTION_ENABLED` | Exactly `true`; absent/other values keep Production closed. |
| `KWS_PRODUCTION_ENVIRONMENT` | Exactly `production`; prevents environment inference. |
| `KWS_PRODUCTION_CLIENT_ID` | Server-only Production client ID. |
| `KWS_PRODUCTION_API_KEY` | Server-only Production API key. |
| `KWS_PRODUCTION_ORG_ID` | Expected Production organization ID. |
| `KWS_PRODUCTION_PRODUCT_ID` | Optional expected Production product ID. |
| `KWS_PRODUCTION_WEBHOOK_SECRETS` | JSON array of 1–4 Production webhook secrets for rotation. |
| `KWS_PRODUCTION_VERIFICATION_SECRETS` | Separate JSON array of 1–4 Production response secrets. |
| `KWS_PRODUCTION_LOCATION_JSON` | Required JSON country/subdivision string (no default). |
| `KWS_PRODUCTION_LANGUAGE` | Optional language; defaults to `en`. |

Production callbacks are `/family/kws/production/webhook` and
`/family/kws/production/response`. They return unavailable while activation is
off or configuration is incomplete. Test callbacks remain under
`/family/kws/test/*` and require the existing isolated Test assertions.

Both environments converge on the same signed-body parsing, freshness checks,
immutable request binding, replay/transaction uniqueness, locking and explicit
activation path. Environment, organization, product and notice are included in
the immutable binding. An authorization created in one environment cannot be
completed or activated in the other.

Migration `a13screen004` already stores the environment and all required binding,
transaction and email-budget data. Production support requires no new schema or
migration. Do not enable Production or alter deployment configuration without a
separate reviewed deployment decision.
