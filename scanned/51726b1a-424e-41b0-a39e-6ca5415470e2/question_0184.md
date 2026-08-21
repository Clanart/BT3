# Q0184: callback: A callback `shop` differing from the cookie-bound shop, testing...

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a callback `shop` differing from the cookie-bound shop, testing whether the session is stored for the wrong store, so that the session may be stored only for the shop cryptographically bound by the validated OAuth callback is violated, leading to account takeover via OAuth code/shop confusion (token theft)? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a callback `shop` differing from the cookie-bound shop, testing whether the session is stored for the wrong store.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: the session may be stored only for the shop cryptographically bound by the validated OAuth callback
- Expected Immunefi impact: account takeover via OAuth code/shop confusion (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
