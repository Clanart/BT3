# Q1735: sanitize_shop_domain: A configured custom `myshopify_domain` appended to `trusted_dom...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a configured custom `myshopify_domain` appended to `trusted_domains`, and a shop crafted to match it instead of the real store, so that the sanitized host must belong to the acting merchant's real myshopify store is violated, leading to cross-shop account/session takeover (unauthorized access to another store's data)? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a configured custom `myshopify_domain` appended to `trusted_domains`, and a shop crafted to match it instead of the real store.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: the sanitized host must belong to the acting merchant's real myshopify store
- Expected Immunefi impact: cross-shop account/session takeover (unauthorized access to another store's data) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
