# Q2229: update_access_scopes?: A nil stored scope compared against configured scopes yielding ...

## Question
Can an unprivileged attacker (the `shop` domain param selecting the stored session to compare) reach `update_access_scopes?` in lib/shopify_app/access_scopes/shop_strategy.rb via legacy scope-change reauth (ShopAccessScopesVerification), supplying a nil stored scope compared against configured scopes yielding a false 'no change', so that scope-change detection must key on the verified shop, not an arbitrary param is violated, leading to scope reauthorization bypass? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/access_scopes/shop_strategy.rb — `update_access_scopes?`
- Entrypoint: legacy scope-change reauth (ShopAccessScopesVerification)
- Attacker controls: the `shop` domain param selecting the stored session to compare — specifically a nil stored scope compared against configured scopes yielding a false 'no change'.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: scope-change detection must key on the verified shop, not an arbitrary param
- Expected Immunefi impact: scope reauthorization bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
