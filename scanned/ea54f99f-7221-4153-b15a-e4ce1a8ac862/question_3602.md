# Q3602: store: A `shopify_domain` differing only by case from an existing row,...

## Question
Can an unprivileged attacker (the `shopify_domain` used as the unique key and its casing) reach `store / retrieve_by_shopify_domain / construct_session` in lib/shopify_app/session/shop_session_storage.rb via any store/retrieve triggered by callback save_session or token exchange, supplying a `shopify_domain` differing only by case from an existing row, testing the `case_sensitive: false` uniqueness vs lookup, so that one shop row must map to exactly one canonical domain; lookup and uniqueness must agree is violated, leading to cross-shop token retrieval (access-token theft) via key normalization mismatch? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `store / retrieve_by_shopify_domain / construct_session`
- Entrypoint: any store/retrieve triggered by callback save_session or token exchange
- Attacker controls: the `shopify_domain` used as the unique key and its casing — specifically a `shopify_domain` differing only by case from an existing row, testing the `case_sensitive: false` uniqueness vs lookup.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: one shop row must map to exactly one canonical domain; lookup and uniqueness must agree
- Expected Immunefi impact: cross-shop token retrieval (access-token theft) via key normalization mismatch (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
