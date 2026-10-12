# AcmePay Overview

AcmePay is Acme Corp's internal payment gateway for Southeast Asia. It handles
card, QRIS, and virtual-account payments for merchants onboarded through the
Acme Merchant Portal.

## Key facts
- Launched: March 2025, starting in Indonesia.
- Supported rails: Visa, Mastercard, QRIS, bank virtual accounts (BCA, Mandiri, BRI, BNI).
- Settlement: T+1 for cards, T+0 for QRIS.
- SLA: 99.95% monthly uptime on the authorization API.
- Observability: every transaction emits an OpenTelemetry trace ingested into Dynatrace.

## Contacts
- Platform owner: Payments Platform team.
- On-call: pay-oncall@acme.example.
