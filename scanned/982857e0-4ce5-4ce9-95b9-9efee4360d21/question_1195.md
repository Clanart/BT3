# Q1195: covers_scopes?: A scope string that `AuthScopes#covers?` treats as a superset d...

## Question
Can an unprivileged attacker (the `shop`/`shopify_user_id` param that selects which stored session's scope is compared) reach `covers_scopes? / update_access_scopes?` in lib/shopify_app/access_scopes/user_strategy.rb via activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled), supplying a scope string that `AuthScopes#covers?` treats as a superset due to normalization/whitespace, so that scope coverage must reflect the acting session's real grants; empty must not mean sufficient is violated, leading to privilege/scope check bypass (unauthorized API scope use)? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/access_scopes/user_strategy.rb — `covers_scopes? / update_access_scopes?`
- Entrypoint: activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled)
- Attacker controls: the `shop`/`shopify_user_id` param that selects which stored session's scope is compared — specifically a scope string that `AuthScopes#covers?` treats as a superset due to normalization/whitespace.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: scope coverage must reflect the acting session's real grants; empty must not mean sufficient
- Expected Immunefi impact: privilege/scope check bypass (unauthorized API scope use) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
