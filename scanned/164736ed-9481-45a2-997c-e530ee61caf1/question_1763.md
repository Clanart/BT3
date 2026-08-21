# Q1763: sanitize_shop_domain: A shop value with an IDN/punycode homoglyph of `myshopify.com` ...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value with an IDN/punycode homoglyph of `myshopify.com` that normalizes to a look-alike host, so that nil return must fail closed, never fall through to a default/wildcard shop is violated, leading to authentication bypass into an unintended shop context? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value with an IDN/punycode homoglyph of `myshopify.com` that normalizes to a look-alike host.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: nil return must fail closed, never fall through to a default/wildcard shop
- Expected Immunefi impact: authentication bypass into an unintended shop context (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
