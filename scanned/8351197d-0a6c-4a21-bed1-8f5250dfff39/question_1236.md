# Q1236: check_shop_domain: A blank `shop` so `current_shopify_domain` returns nil and `che...

## Question
Can an unprivileged attacker (the `shop` param and `host`/`embedded` params) reach `check_shop_domain / current_shopify_domain / installed_shop_session` in app/controllers/concerns/shopify_app/ensure_installed.rb via GET any EnsureInstalled controller action, supplying a blank `shop` so `current_shopify_domain` returns nil and `check_shop_domain` only redirects (probe fail-open elsewhere), so that the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param is violated, leading to cross-shop access: loading/acting with another store's offline session? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_installed.rb — `check_shop_domain / current_shopify_domain / installed_shop_session`
- Entrypoint: GET any EnsureInstalled controller action
- Attacker controls: the `shop` param and `host`/`embedded` params — specifically a blank `shop` so `current_shopify_domain` returns nil and `check_shop_domain` only redirects (probe fail-open elsewhere).
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param
- Expected Immunefi impact: cross-shop access: loading/acting with another store's offline session (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
