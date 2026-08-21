# Q3144: sanitize_shop_domain: A configured custom `myshopify_domain` appended to `trusted_dom...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a configured custom `myshopify_domain` appended to `trusted_domains`, and a shop crafted to match it instead of the real store, so that a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted is violated, leading to open redirect / OAuth-token leak to an attacker-controlled origin? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a configured custom `myshopify_domain` appended to `trusted_domains`, and a shop crafted to match it instead of the real store.
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted
- Expected Immunefi impact: open redirect / OAuth-token leak to an attacker-controlled origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
