# AcmePay API & Onboarding

## Authentication
AcmePay uses API keys scoped per merchant. Keys are issued in the Merchant
Portal under Settings > API Keys. Each request must send the key in the
`X-Acme-Key` header. Keys can be rotated without downtime; the old key stays
valid for 24 hours after rotation.

## Core endpoints
- `POST /v1/charges` — create a charge. Required: amount (minor units), currency, rail, merchant_ref.
- `GET /v1/charges/{id}` — fetch charge status (pending, authorized, settled, failed).
- `POST /v1/refunds` — refund a settled charge, full or partial.

## Pricing
- Cards: 2.9% + IDR 2,000 per successful transaction.
- QRIS: 0.7% per successful transaction.
- Virtual account: flat IDR 4,000 per successful transaction.
- No setup fee; no monthly minimum. Refunds are free.

## Onboarding steps
1. Sign the merchant agreement in the portal.
2. Complete KYB (business verification), usually 1-2 business days.
3. Generate a sandbox API key and test against the sandbox base URL.
4. Request production access; a reviewer approves within 1 business day.
