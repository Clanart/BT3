# Q1083: receive: A `params.permit!` mass-assignment surface after verification, ...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a `params.permit!` mass-assignment surface after verification, injecting unexpected params into downstream handlers, so that no webhook side effect may run before HMAC verification succeeds over the raw body is violated, leading to forged webhook processing (unauthorized state change)? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a `params.permit!` mass-assignment surface after verification, injecting unexpected params into downstream handlers.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: no webhook side effect may run before HMAC verification succeeds over the raw body
- Expected Immunefi impact: forged webhook processing (unauthorized state change) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
