# Q3562: covers_scopes?: A session whose `scope` is empty so `covers_scopes?` short-circ...

## Question
Can an unprivileged attacker (the `shop`/`shopify_user_id` param that selects which stored session's scope is compared) reach `covers_scopes? / update_access_scopes?` in lib/shopify_app/access_scopes/user_strategy.rb via activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled), supplying a session whose `scope` is empty so `covers_scopes?` short-circuits to true, skipping re-auth despite missing grants, so that scope coverage must reflect the acting session's real grants; empty must not mean sufficient is violated, leading to privilege/scope check bypass (unauthorized API scope use)? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: lib/shopify_app/access_scopes/user_strategy.rb — `covers_scopes? / update_access_scopes?`
- Entrypoint: activate_shopify_session scope re-auth check (reauth_on_access_scope_changes enabled)
- Attacker controls: the `shop`/`shopify_user_id` param that selects which stored session's scope is compared — specifically a session whose `scope` is empty so `covers_scopes?` short-circuits to true, skipping re-auth despite missing grants.
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: scope coverage must reflect the acting session's real grants; empty must not mean sufficient
- Expected Immunefi impact: privilege/scope check bypass (unauthorized API scope use) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
