# Q2939: store: An AssociatedUser reconstructed with `account_owner:false` mask...

## Question
Can an unprivileged attacker (the `shopify_user_id` used as key and the associated `shopify_domain`) reach `store / retrieve_by_shopify_user_id / construct_session` in lib/shopify_app/session/user_session_storage.rb via online-token flows storing/loading a user session, supplying an AssociatedUser reconstructed with `account_owner:false` masking a real owner's privileges, so that a user session must be keyed and loaded so it can never bind one user id to another shop's token is violated, leading to cross-user / cross-shop session confusion (acting as another user)? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/session/user_session_storage.rb — `store / retrieve_by_shopify_user_id / construct_session`
- Entrypoint: online-token flows storing/loading a user session
- Attacker controls: the `shopify_user_id` used as key and the associated `shopify_domain` — specifically an AssociatedUser reconstructed with `account_owner:false` masking a real owner's privileges.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: a user session must be keyed and loaded so it can never bind one user id to another shop's token
- Expected Immunefi impact: cross-user / cross-shop session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
