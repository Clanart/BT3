# Q1935: callback: A non-ShopifyAPI error raised in `validated_auth_objects` that ...

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a non-ShopifyAPI error raised in `validated_auth_objects` that is re-raised vs rescued (info leak in the 500), so that callback state/hmac must be validated before any session persistence is violated, leading to authentication bypass / cross-shop token storage? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a non-ShopifyAPI error raised in `validated_auth_objects` that is re-raised vs rescued (info leak in the 500).
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: callback state/hmac must be validated before any session persistence
- Expected Immunefi impact: authentication bypass / cross-shop token storage (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
