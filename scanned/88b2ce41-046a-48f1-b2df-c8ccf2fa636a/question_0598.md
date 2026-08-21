# Q0598: login_on_scope_changes: An `embedded_param?` toggle steering between `redirect_for_embe...

## Question
Can an unprivileged attacker (the `shop` param, `host`, and Referer) reach `login_on_scope_changes / scopes_mismatch? / shop_login` in app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb via GET an action including ShopAccessScopesVerification (legacy strategy), supplying an `embedded_param?` toggle steering between `redirect_for_embedded` and a plain redirect, so that scope checks and the resulting login redirect must be bound to the verified shop, not a raw param is violated, leading to open redirect / cross-shop reauthorization against an attacker store? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb — `login_on_scope_changes / scopes_mismatch? / shop_login`
- Entrypoint: GET an action including ShopAccessScopesVerification (legacy strategy)
- Attacker controls: the `shop` param, `host`, and Referer — specifically an `embedded_param?` toggle steering between `redirect_for_embedded` and a plain redirect.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: scope checks and the resulting login redirect must be bound to the verified shop, not a raw param
- Expected Immunefi impact: open redirect / cross-shop reauthorization against an attacker store (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
