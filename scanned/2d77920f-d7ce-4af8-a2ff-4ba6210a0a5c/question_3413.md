# Q3413: callback: A callback with attacker `hmac`/`timestamp` to see if signature...

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a callback with attacker `hmac`/`timestamp` to see if signature validation is enforced before `save_session`, so that the session may be stored only for the shop cryptographically bound by the validated OAuth callback is violated, leading to account takeover via OAuth code/shop confusion (token theft)? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a callback with attacker `hmac`/`timestamp` to see if signature validation is enforced before `save_session`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the session may be stored only for the shop cryptographically bound by the validated OAuth callback
- Expected Immunefi impact: account takeover via OAuth code/shop confusion (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
