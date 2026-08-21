# Q0019: sanitize_shop_domain: A shop value `admin.shopify.com/store/victim/../attacker` abusi...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value `admin.shopify.com/store/victim/../attacker` abusing `uri.path.split('/').last` in unified-admin extraction, so that the sanitized host must belong to the acting merchant's real myshopify store is violated, leading to cross-shop account/session takeover (unauthorized access to another store's data)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value `admin.shopify.com/store/victim/../attacker` abusing `uri.path.split('/').last` in unified-admin extraction.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: the sanitized host must belong to the acting merchant's real myshopify store
- Expected Immunefi impact: cross-shop account/session takeover (unauthorized access to another store's data) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
