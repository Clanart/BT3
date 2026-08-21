# Q1289: sanitize_shop_domain: A shop value using a non-.com trusted TLD like `shop.myshopify....

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value using a non-.com trusted TLD like `shop.myshopify.io` or `shop.spin.dev` to widen the accepted set, so that the sanitized host must belong to the acting merchant's real myshopify store is violated, leading to cross-shop account/session takeover (unauthorized access to another store's data)? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value using a non-.com trusted TLD like `shop.myshopify.io` or `shop.spin.dev` to widen the accepted set.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: the sanitized host must belong to the acting merchant's real myshopify store
- Expected Immunefi impact: cross-shop account/session takeover (unauthorized access to another store's data) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
