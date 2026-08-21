# Q1987: request_payment: A `return_url = ...?shop=#{shop}&host=#{host}` built from `sess...

## Question
Can an unprivileged attacker (`shop`/`host` params that flow into the billing return_url and confirmation redirect) reach `request_payment / check_billing redirect` in lib/shopify_app/controller_concerns/ensure_billing.rb via GET a billing-gated action without active payment, supplying a `return_url = ...?shop=#{shop}&host=#{host}` built from `session.shop` where shop is attacker-influenced, so that billing redirect/return URLs must be bound to the app + verified shop origin is violated, leading to open redirect via billing confirmation flow? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/controller_concerns/ensure_billing.rb — `request_payment / check_billing redirect`
- Entrypoint: GET a billing-gated action without active payment
- Attacker controls: `shop`/`host` params that flow into the billing return_url and confirmation redirect — specifically a `return_url = ...?shop=#{shop}&host=#{host}` built from `session.shop` where shop is attacker-influenced.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: billing redirect/return URLs must be bound to the app + verified shop origin
- Expected Immunefi impact: open redirect via billing confirmation flow (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
