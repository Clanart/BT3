# Q1780: callback: A non-ShopifyAPI error raised in `validated_auth_objects` that ...

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a non-ShopifyAPI error raised in `validated_auth_objects` that is re-raised vs rescued (info leak in the 500), so that callback state/hmac must be validated before any session persistence is violated, leading to authentication bypass / cross-shop token storage? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a non-ShopifyAPI error raised in `validated_auth_objects` that is re-raised vs rescued (info leak in the 500).
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: callback state/hmac must be validated before any session persistence
- Expected Immunefi impact: authentication bypass / cross-shop token storage (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
