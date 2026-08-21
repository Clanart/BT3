# Q3437: callback: A replayed authorization `code` to mint a second session

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a replayed authorization `code` to mint a second session, so that the session may be stored only for the shop cryptographically bound by the validated OAuth callback is violated, leading to account takeover via OAuth code/shop confusion (token theft)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a replayed authorization `code` to mint a second session.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: the session may be stored only for the shop cryptographically bound by the validated OAuth callback
- Expected Immunefi impact: account takeover via OAuth code/shop confusion (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
