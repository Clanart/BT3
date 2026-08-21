# Q1155: login_on_scope_changes: A `shop` param whose scope lookup (`update_access_scopes?`) is ...

## Question
Can an unprivileged attacker (the `shop` param, `host`, and Referer) reach `login_on_scope_changes / scopes_mismatch? / shop_login` in app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb via GET an action including ShopAccessScopesVerification (legacy strategy), supplying a `shop` param whose scope lookup (`update_access_scopes?`) is done on an attacker domain, forcing a login redirect with attacker host, so that scope checks and the resulting login redirect must be bound to the verified shop, not a raw param is violated, leading to open redirect / cross-shop reauthorization against an attacker store? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb — `login_on_scope_changes / scopes_mismatch? / shop_login`
- Entrypoint: GET an action including ShopAccessScopesVerification (legacy strategy)
- Attacker controls: the `shop` param, `host`, and Referer — specifically a `shop` param whose scope lookup (`update_access_scopes?`) is done on an attacker domain, forcing a login redirect with attacker host.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: scope checks and the resulting login redirect must be bound to the verified shop, not a raw param
- Expected Immunefi impact: open redirect / cross-shop reauthorization against an attacker store (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
