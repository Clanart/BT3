# Q0217: store: A retrieve_by_shopify_user_id returning a session whose `shopif...

## Question
Can an unprivileged attacker (the `shopify_user_id` used as key and the associated `shopify_domain`) reach `store / retrieve_by_shopify_user_id / construct_session` in lib/shopify_app/session/user_session_storage.rb via online-token flows storing/loading a user session, supplying a retrieve_by_shopify_user_id returning a session whose `shopify_domain` no longer matches the caller's shop, so that a user session must be keyed and loaded so it can never bind one user id to another shop's token is violated, leading to cross-user / cross-shop session confusion (acting as another user)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/session/user_session_storage.rb — `store / retrieve_by_shopify_user_id / construct_session`
- Entrypoint: online-token flows storing/loading a user session
- Attacker controls: the `shopify_user_id` used as key and the associated `shopify_domain` — specifically a retrieve_by_shopify_user_id returning a session whose `shopify_domain` no longer matches the caller's shop.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: a user session must be keyed and loaded so it can never bind one user id to another shop's token
- Expected Immunefi impact: cross-user / cross-shop session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
