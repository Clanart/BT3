# Q2626: receive: A `params.permit!` mass-assignment surface after verification, ...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a `params.permit!` mass-assignment surface after verification, injecting unexpected params into downstream handlers, so that the shop-domain header must not be trusted as identity without HMAC binding is violated, leading to cross-shop data spoofing via forged webhook headers? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a `params.permit!` mass-assignment surface after verification, injecting unexpected params into downstream handlers.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: the shop-domain header must not be trusted as identity without HMAC binding
- Expected Immunefi impact: cross-shop data spoofing via forged webhook headers (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
