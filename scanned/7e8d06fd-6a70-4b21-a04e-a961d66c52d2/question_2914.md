# Q2914: check_shop_domain: The legacy `validate_non_embedded_session` REST `get('shop')` p...

## Question
Can an unprivileged attacker (the `shop` param and `host`/`embedded` params) reach `check_shop_domain / current_shopify_domain / installed_shop_session` in app/controllers/concerns/shopify_app/ensure_installed.rb via GET any EnsureInstalled controller action, supplying the legacy `validate_non_embedded_session` REST `get('shop')` performed with `installed_shop_session` of an attacker-chosen shop, so that the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param is violated, leading to cross-shop access: loading/acting with another store's offline session? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_installed.rb — `check_shop_domain / current_shopify_domain / installed_shop_session`
- Entrypoint: GET any EnsureInstalled controller action
- Attacker controls: the `shop` param and `host`/`embedded` params — specifically the legacy `validate_non_embedded_session` REST `get('shop')` performed with `installed_shop_session` of an attacker-chosen shop.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param
- Expected Immunefi impact: cross-shop access: loading/acting with another store's offline session (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
