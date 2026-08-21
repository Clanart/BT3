# Q0330: new: A `return_to` copied to session then reflected by `return_addre...

## Question
Can an unprivileged attacker (`shop`, `return_to`, `host`, `top_level`, `embedded`, and the Referer header) reach `new / create / authenticate / start_install / start_oauth` in app/controllers/shopify_app/sessions_controller.rb via GET/POST /login (public, unauthenticated), supplying a `return_to` copied to session then reflected by `return_address` after callback, so that the install/OAuth target store must be the sanitized, attacker-independent shop, and return_to same-origin is violated, leading to OAuth flow initiated against an attacker-chosen store / open redirect on login? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: app/controllers/shopify_app/sessions_controller.rb — `new / create / authenticate / start_install / start_oauth`
- Entrypoint: GET/POST /login (public, unauthenticated)
- Attacker controls: `shop`, `return_to`, `host`, `top_level`, `embedded`, and the Referer header — specifically a `return_to` copied to session then reflected by `return_address` after callback.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: the install/OAuth target store must be the sanitized, attacker-independent shop, and return_to same-origin
- Expected Immunefi impact: OAuth flow initiated against an attacker-chosen store / open redirect on login (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
