# ScholarForge license server

A small, separate service for ScholarForge Pro subscriptions ($20/month). It never receives book content.

Stores: customers (provider reference, **hashed** e-mail), subscriptions (status, period end), licenses
(**hashed** keys with a server pepper), device activations (hashed device ids), processed webhook ids
(idempotency). SQLite with `secure_delete`. Logs contain no keys or e-mails.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/checkout` | start a subscription checkout (returns the provider URL) |
| GET | `/v1/checkout/result?session=` | show the new license key once after payment |
| POST | `/v1/webhooks/{provider}` | provider events (signature-verified, idempotent) |
| POST | `/v1/licenses/activate` | `{license_key, device_id, app_version}` → signed token |
| POST | `/v1/licenses/refresh` | new token for an activated device |
| POST | `/v1/licenses/deactivate` | release a device |
| GET | `/healthz` | liveness |

Tokens: `SF1.<payload>.<Ed25519 signature>`; validity = paid period + 3 days; offline grace 14 days.
Max 3 devices per license. Cancellation keeps access until the end of the paid period.

## Run locally (test provider, no money)

```bash
pip install -e . -e license_server
scholarforge-license-server keys --out signing-key.pem       # prints the public key
export SF_LS_SIGNING_KEY_FILE=signing-key.pem SF_LS_PEPPER=$(python -c "import secrets;print(secrets.token_hex(24))")
scholarforge-license-server serve --port 8787
```

Client side: put the printed public key into `src/scholarforge/licensing/keys.py` (or
`SCHOLARFORGE_LICENSE_PUBKEYS`), set `license.server_url`, and `license.enforcement: on` for releases.

## Stripe (production)

Set `SF_LS_PROVIDER=stripe`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` (endpoint signing secret) and
`STRIPE_PRICE_ID` (a recurring $20/month price). Subscribe the webhook endpoint
`https://<host>/v1/webhooks/stripe` to `checkout.session.completed`, `customer.subscription.created`,
`customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid`, `invoice.payment_failed`.
The implementation verifies `Stripe-Signature` (HMAC-SHA256 of `t.payload`, 300 s tolerance) and was
tested only with locally signed payloads — run an end-to-end test in Stripe test mode before launch, and
consider a merchant of record for VAT/sales tax. Deploy behind HTTPS; back up the database and the
signing key separately.
