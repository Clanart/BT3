# Q0005: request_payment: A `fullpage_redirect_to(confirmation_url)` where confirmation_u...

## Question
Can an unprivileged attacker (`shop`/`host` params that flow into the billing return_url and confirmation redirect) reach `request_payment / check_billing redirect` in lib/shopify_app/controller_concerns/ensure_billing.rb via GET a billing-gated action without active payment, supplying a `fullpage_redirect_to(confirmation_url)` where confirmation_url origin is not re-validated, so that billing redirect/return URLs must be bound to the app + verified shop origin is violated, leading to open redirect via billing confirmation flow? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/controller_concerns/ensure_billing.rb — `request_payment / check_billing redirect`
- Entrypoint: GET a billing-gated action without active payment
- Attacker controls: `shop`/`host` params that flow into the billing return_url and confirmation redirect — specifically a `fullpage_redirect_to(confirmation_url)` where confirmation_url origin is not re-validated.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: billing redirect/return URLs must be bound to the app + verified shop origin
- Expected Immunefi impact: open redirect via billing confirmation flow (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
