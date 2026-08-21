# Q1461: covers_scopes?: A scope string that `AuthScopes#covers?` treats as a superset d...

## Question
Can an unprivileged attacker (the `shop`/`shopify_user_id` param that selects which stored session's scope is compared) reach `covers_scopes? / update_access_scopes?` in lib/shopify_app/access_scopes/user_strategy.rb via activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled), supplying a scope string that `AuthScopes#covers?` treats as a superset due to normalization/whitespace, so that scope coverage must reflect the acting session's real grants; empty must not mean sufficient is violated, leading to privilege/scope check bypass (unauthorized API scope use)? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/access_scopes/user_strategy.rb — `covers_scopes? / update_access_scopes?`
- Entrypoint: activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled)
- Attacker controls: the `shop`/`shopify_user_id` param that selects which stored session's scope is compared — specifically a scope string that `AuthScopes#covers?` treats as a superset due to normalization/whitespace.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: scope coverage must reflect the acting session's real grants; empty must not mean sufficient
- Expected Immunefi impact: privilege/scope check bypass (unauthorized API scope use) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
