# Q3254: receive: A `:type` value that maps to an unintended registered webhook h...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a `:type` value that maps to an unintended registered webhook handler, so that no webhook side effect may run before HMAC verification succeeds over the raw body is violated, leading to forged webhook processing (unauthorized state change)? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a `:type` value that maps to an unintended registered webhook handler.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: no webhook side effect may run before HMAC verification succeeds over the raw body
- Expected Immunefi impact: forged webhook processing (unauthorized state change) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
