# Q3021: update_access_scopes?: A `shop` param pointing to a session whose scope matches config...

## Question
Can an unprivileged attacker (the `shop` domain param selecting the stored session to compare) reach `update_access_scopes?` in lib/shopify_app/access_scopes/shop_strategy.rb via legacy scope-change reauth (ShopAccessScopesVerification), supplying a `shop` param pointing to a session whose scope matches config, suppressing a needed re-auth for the real shop, so that scope-change detection must key on the verified shop, not an arbitrary param is violated, leading to scope reauthorization bypass? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: lib/shopify_app/access_scopes/shop_strategy.rb — `update_access_scopes?`
- Entrypoint: legacy scope-change reauth (ShopAccessScopesVerification)
- Attacker controls: the `shop` domain param selecting the stored session to compare — specifically a `shop` param pointing to a session whose scope matches config, suppressing a needed re-auth for the real shop.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: scope-change detection must key on the verified shop, not an arbitrary param
- Expected Immunefi impact: scope reauthorization bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
