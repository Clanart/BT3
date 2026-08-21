# Q0142: store: A `find_or_initialize_by(shopify_domain:)` race where two concu...

## Question
Can an unprivileged attacker (the `shopify_domain` used as the unique key and its casing) reach `store / retrieve_by_shopify_domain / construct_session` in lib/shopify_app/session/shop_session_storage.rb via any store/retrieve triggered by callback save_session or token exchange, supplying a `find_or_initialize_by(shopify_domain:)` race where two concurrent stores collide on the same shop, so that one shop row must map to exactly one canonical domain; lookup and uniqueness must agree is violated, leading to cross-shop token retrieval (access-token theft) via key normalization mismatch? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `store / retrieve_by_shopify_domain / construct_session`
- Entrypoint: any store/retrieve triggered by callback save_session or token exchange
- Attacker controls: the `shopify_domain` used as the unique key and its casing — specifically a `find_or_initialize_by(shopify_domain:)` race where two concurrent stores collide on the same shop.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: one shop row must map to exactly one canonical domain; lookup and uniqueness must agree
- Expected Immunefi impact: cross-shop token retrieval (access-token theft) via key normalization mismatch (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
