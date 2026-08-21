# Q2635: login_on_scope_changes: An `embedded_param?` toggle steering between `redirect_for_embe...

## Question
Can an unprivileged attacker (the `shop` param, `host`, and Referer) reach `login_on_scope_changes / scopes_mismatch? / shop_login` in app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb via GET an action including ShopAccessScopesVerification (legacy strategy), supplying an `embedded_param?` toggle steering between `redirect_for_embedded` and a plain redirect, so that scope checks and the resulting login redirect must be bound to the verified shop, not a raw param is violated, leading to open redirect / cross-shop reauthorization against an attacker store? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb — `login_on_scope_changes / scopes_mismatch? / shop_login`
- Entrypoint: GET an action including ShopAccessScopesVerification (legacy strategy)
- Attacker controls: the `shop` param, `host`, and Referer — specifically an `embedded_param?` toggle steering between `redirect_for_embedded` and a plain redirect.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: scope checks and the resulting login redirect must be bound to the verified shop, not a raw param
- Expected Immunefi impact: open redirect / cross-shop reauthorization against an attacker store (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
