# Q1202: callback: A replayed authorization `code` to mint a second session

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a replayed authorization `code` to mint a second session, so that the session may be stored only for the shop cryptographically bound by the validated OAuth callback is violated, leading to account takeover via OAuth code/shop confusion (token theft)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a replayed authorization `code` to mint a second session.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: the session may be stored only for the shop cryptographically bound by the validated OAuth callback
- Expected Immunefi impact: account takeover via OAuth code/shop confusion (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
