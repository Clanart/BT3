# Q2178: sanitize_shop_domain: A shop value containing no dot so `name += .myshopify.com` sile...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value containing no dot so `name += .myshopify.com` silently reattaches an attacker-chosen label, so that nil return must fail closed, never fall through to a default/wildcard shop is violated, leading to authentication bypass into an unintended shop context? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value containing no dot so `name += .myshopify.com` silently reattaches an attacker-chosen label.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: nil return must fail closed, never fall through to a default/wildcard shop
- Expected Immunefi impact: authentication bypass into an unintended shop context (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
