# Q2201: sanitize_shop_domain: A shop value with leading/trailing whitespace or control bytes ...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value with leading/trailing whitespace or control bytes stripped only by `.strip`, so that a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted is violated, leading to open redirect / OAuth-token leak to an attacker-controlled origin? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value with leading/trailing whitespace or control bytes stripped only by `.strip`.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted
- Expected Immunefi impact: open redirect / OAuth-token leak to an attacker-controlled origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
