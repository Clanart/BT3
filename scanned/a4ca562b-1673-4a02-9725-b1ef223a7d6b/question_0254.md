# Q0254: request_payment: A confirmation_url from `request_payment` reflected into `add_a...

## Question
Can an unprivileged attacker (`shop`/`host` params that flow into the billing return_url and confirmation redirect) reach `request_payment / check_billing redirect` in lib/shopify_app/controller_concerns/ensure_billing.rb via GET a billing-gated action without active payment, supplying a confirmation_url from `request_payment` reflected into `add_app_bridge_redirect_url_header` on XHR, so that billing redirect/return URLs must be bound to the app + verified shop origin is violated, leading to open redirect via billing confirmation flow? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/ensure_billing.rb — `request_payment / check_billing redirect`
- Entrypoint: GET a billing-gated action without active payment
- Attacker controls: `shop`/`host` params that flow into the billing return_url and confirmation redirect — specifically a confirmation_url from `request_payment` reflected into `add_app_bridge_redirect_url_header` on XHR.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: billing redirect/return URLs must be bound to the app + verified shop origin
- Expected Immunefi impact: open redirect via billing confirmation flow (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
