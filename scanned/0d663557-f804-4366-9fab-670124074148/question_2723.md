# Q2723: receive: A forged body that passes only if HMAC verification is bypassed...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a forged body that passes only if HMAC verification is bypassed (tied to hmac_valid?), so that the shop-domain header must not be trusted as identity without HMAC binding is violated, leading to cross-shop data spoofing via forged webhook headers? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a forged body that passes only if HMAC verification is bypassed (tied to hmac_valid?).
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: the shop-domain header must not be trusted as identity without HMAC binding
- Expected Immunefi impact: cross-shop data spoofing via forged webhook headers (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
