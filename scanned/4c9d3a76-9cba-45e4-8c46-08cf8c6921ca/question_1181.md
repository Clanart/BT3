# Q1181: update_access_scopes?: A `shop` param pointing to a session whose scope matches config...

## Question
Can an unprivileged attacker (the `shop` domain param selecting the stored session to compare) reach `update_access_scopes?` in lib/shopify_app/access_scopes/shop_strategy.rb via legacy scope-change reauth (ShopAccessScopesVerification), supplying a `shop` param pointing to a session whose scope matches config, suppressing a needed re-auth for the real shop, so that scope-change detection must key on the verified shop, not an arbitrary param is violated, leading to scope reauthorization bypass? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: lib/shopify_app/access_scopes/shop_strategy.rb — `update_access_scopes?`
- Entrypoint: legacy scope-change reauth (ShopAccessScopesVerification)
- Attacker controls: the `shop` domain param selecting the stored session to compare — specifically a `shop` param pointing to a session whose scope matches config, suppressing a needed re-auth for the real shop.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: scope-change detection must key on the verified shop, not an arbitrary param
- Expected Immunefi impact: scope reauthorization bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
