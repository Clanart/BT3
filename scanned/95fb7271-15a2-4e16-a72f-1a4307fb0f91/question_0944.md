# Q0944: callback: A callback `shop` differing from the cookie-bound shop, testing...

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a callback `shop` differing from the cookie-bound shop, testing whether the session is stored for the wrong store, so that callback state/hmac must be validated before any session persistence is violated, leading to authentication bypass / cross-shop token storage? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a callback `shop` differing from the cookie-bound shop, testing whether the session is stored for the wrong store.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: callback state/hmac must be validated before any session persistence
- Expected Immunefi impact: authentication bypass / cross-shop token storage (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
