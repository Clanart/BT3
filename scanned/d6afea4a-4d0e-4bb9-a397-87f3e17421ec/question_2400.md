# Q2400: check_shop_domain: A request toggling `use_new_embedded_auth_strategy?` behavior b...

## Question
Can an unprivileged attacker (the `shop` param and `host`/`embedded` params) reach `check_shop_domain / current_shopify_domain / installed_shop_session` in app/controllers/concerns/shopify_app/ensure_installed.rb via GET any EnsureInstalled controller action, supplying a request toggling `use_new_embedded_auth_strategy?` behavior between token-exchange and legacy shop-known checks, so that the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param is violated, leading to cross-shop access: loading/acting with another store's offline session? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_installed.rb — `check_shop_domain / current_shopify_domain / installed_shop_session`
- Entrypoint: GET any EnsureInstalled controller action
- Attacker controls: the `shop` param and `host`/`embedded` params — specifically a request toggling `use_new_embedded_auth_strategy?` behavior between token-exchange and legacy shop-known checks.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param
- Expected Immunefi impact: cross-shop access: loading/acting with another store's offline session (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
