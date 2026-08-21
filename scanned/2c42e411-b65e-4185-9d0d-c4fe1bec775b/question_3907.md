# Q3907: covers_scopes?: A mismatch between `configuration_access_scopes` and stored sco...

## Question
Can an unprivileged attacker (the `shop`/`shopify_user_id` param that selects which stored session's scope is compared) reach `covers_scopes? / update_access_scopes?` in lib/shopify_app/access_scopes/user_strategy.rb via activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled), supplying a mismatch between `configuration_access_scopes` and stored scopes that is silently accepted, so that scope coverage must reflect the acting session's real grants; empty must not mean sufficient is violated, leading to privilege/scope check bypass (unauthorized API scope use)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/access_scopes/user_strategy.rb — `covers_scopes? / update_access_scopes?`
- Entrypoint: activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled)
- Attacker controls: the `shop`/`shopify_user_id` param that selects which stored session's scope is compared — specifically a mismatch between `configuration_access_scopes` and stored scopes that is silently accepted.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: scope coverage must reflect the acting session's real grants; empty must not mean sufficient
- Expected Immunefi impact: privilege/scope check bypass (unauthorized API scope use) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
