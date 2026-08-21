# Q0179: receive: A `:type` value that maps to an unintended registered webhook h...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a `:type` value that maps to an unintended registered webhook handler, so that the shop-domain header must not be trusted as identity without HMAC binding is violated, leading to cross-shop data spoofing via forged webhook headers? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a `:type` value that maps to an unintended registered webhook handler.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: the shop-domain header must not be trusted as identity without HMAC binding
- Expected Immunefi impact: cross-shop data spoofing via forged webhook headers (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
