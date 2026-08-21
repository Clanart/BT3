# Q1851: receive: A `:type` value that maps to an unintended registered webhook h...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a `:type` value that maps to an unintended registered webhook handler, so that no webhook side effect may run before HMAC verification succeeds over the raw body is violated, leading to forged webhook processing (unauthorized state change)? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a `:type` value that maps to an unintended registered webhook handler.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: no webhook side effect may run before HMAC verification succeeds over the raw body
- Expected Immunefi impact: forged webhook processing (unauthorized state change) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
