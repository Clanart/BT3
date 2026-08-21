# Q2958: store: A `shopify_user_id` collision across two different shops sharin...

## Question
Can an unprivileged attacker (the `shopify_user_id` used as key and the associated `shopify_domain`) reach `store / retrieve_by_shopify_user_id / construct_session` in lib/shopify_app/session/user_session_storage.rb via online-token flows storing/loading a user session, supplying a `shopify_user_id` collision across two different shops sharing the same numeric user id, so that a user session must be keyed and loaded so it can never bind one user id to another shop's token is violated, leading to cross-user / cross-shop session confusion (acting as another user)? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/session/user_session_storage.rb — `store / retrieve_by_shopify_user_id / construct_session`
- Entrypoint: online-token flows storing/loading a user session
- Attacker controls: the `shopify_user_id` used as key and the associated `shopify_domain` — specifically a `shopify_user_id` collision across two different shops sharing the same numeric user id.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: a user session must be keyed and loaded so it can never bind one user id to another shop's token
- Expected Immunefi impact: cross-user / cross-shop session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
