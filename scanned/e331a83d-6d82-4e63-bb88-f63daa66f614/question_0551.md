# Q0551: covers_scopes?: A `shopify_user_id` param selecting another user's session for ...

## Question
Can an unprivileged attacker (the `shop`/`shopify_user_id` param that selects which stored session's scope is compared) reach `covers_scopes? / update_access_scopes?` in lib/shopify_app/access_scopes/user_strategy.rb via activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled), supplying a `shopify_user_id` param selecting another user's session for the scope comparison, so that scope coverage must reflect the acting session's real grants; empty must not mean sufficient is violated, leading to privilege/scope check bypass (unauthorized API scope use)? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/access_scopes/user_strategy.rb — `covers_scopes? / update_access_scopes?`
- Entrypoint: activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled)
- Attacker controls: the `shop`/`shopify_user_id` param that selects which stored session's scope is compared — specifically a `shopify_user_id` param selecting another user's session for the scope comparison.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: scope coverage must reflect the acting session's real grants; empty must not mean sufficient
- Expected Immunefi impact: privilege/scope check bypass (unauthorized API scope use) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
