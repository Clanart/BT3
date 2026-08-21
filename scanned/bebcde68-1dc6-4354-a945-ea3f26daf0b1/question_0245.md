# Q0245: check_shop_domain: A `shop` that sanitizes to another merchant's domain so `instal...

## Question
Can an unprivileged attacker (the `shop` param and `host`/`embedded` params) reach `check_shop_domain / current_shopify_domain / installed_shop_session` in app/controllers/concerns/shopify_app/ensure_installed.rb via GET any EnsureInstalled controller action, supplying a `shop` that sanitizes to another merchant's domain so `installed_shop_session` loads that store's offline session, so that the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param is violated, leading to cross-shop access: loading/acting with another store's offline session? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_installed.rb — `check_shop_domain / current_shopify_domain / installed_shop_session`
- Entrypoint: GET any EnsureInstalled controller action
- Attacker controls: the `shop` param and `host`/`embedded` params — specifically a `shop` that sanitizes to another merchant's domain so `installed_shop_session` loads that store's offline session.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param
- Expected Immunefi impact: cross-shop access: loading/acting with another store's offline session (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
