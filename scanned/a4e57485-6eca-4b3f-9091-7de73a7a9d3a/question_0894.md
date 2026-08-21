# Q0894: check_shop_domain: The legacy `validate_non_embedded_session` REST `get('shop')` p...

## Question
Can an unprivileged attacker (the `shop` param and `host`/`embedded` params) reach `check_shop_domain / current_shopify_domain / installed_shop_session` in app/controllers/concerns/shopify_app/ensure_installed.rb via GET any EnsureInstalled controller action, supplying the legacy `validate_non_embedded_session` REST `get('shop')` performed with `installed_shop_session` of an attacker-chosen shop, so that the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param is violated, leading to cross-shop access: loading/acting with another store's offline session? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_installed.rb — `check_shop_domain / current_shopify_domain / installed_shop_session`
- Entrypoint: GET any EnsureInstalled controller action
- Attacker controls: the `shop` param and `host`/`embedded` params — specifically the legacy `validate_non_embedded_session` REST `get('shop')` performed with `installed_shop_session` of an attacker-chosen shop.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: the loaded installed shop session must correspond to the merchant proven to control the shop, not a raw param
- Expected Immunefi impact: cross-shop access: loading/acting with another store's offline session (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
