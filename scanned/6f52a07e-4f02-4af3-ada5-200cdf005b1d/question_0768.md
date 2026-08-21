# Q0768: sanitize_shop_domain: A shop value that raises `Addressable::URI::InvalidURIError` so...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value that raises `Addressable::URI::InvalidURIError` so `sanitize_shop_domain` returns nil and a caller treats nil-shop as a wildcard, so that the sanitized host must belong to the acting merchant's real myshopify store is violated, leading to cross-shop account/session takeover (unauthorized access to another store's data)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value that raises `Addressable::URI::InvalidURIError` so `sanitize_shop_domain` returns nil and a caller treats nil-shop as a wildcard.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: the sanitized host must belong to the acting merchant's real myshopify store
- Expected Immunefi impact: cross-shop account/session takeover (unauthorized access to another store's data) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
