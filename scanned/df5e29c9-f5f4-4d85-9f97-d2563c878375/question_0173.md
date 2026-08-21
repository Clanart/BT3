# Q0173: update_access_scopes?: A `shop` param pointing to a session whose scope matches config...

## Question
Can an unprivileged attacker (the `shop` domain param selecting the stored session to compare) reach `update_access_scopes?` in lib/shopify_app/access_scopes/shop_strategy.rb via legacy scope-change reauth (ShopAccessScopesVerification), supplying a `shop` param pointing to a session whose scope matches config, suppressing a needed re-auth for the real shop, so that scope-change detection must key on the verified shop, not an arbitrary param is violated, leading to scope reauthorization bypass? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/access_scopes/shop_strategy.rb — `update_access_scopes?`
- Entrypoint: legacy scope-change reauth (ShopAccessScopesVerification)
- Attacker controls: the `shop` domain param selecting the stored session to compare — specifically a `shop` param pointing to a session whose scope matches config, suppressing a needed re-auth for the real shop.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: scope-change detection must key on the verified shop, not an arbitrary param
- Expected Immunefi impact: scope reauthorization bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
