# Q3993: shopify_id_token: A token with a far-future `exp` combined with `jwt_expire_at`'s...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying a token with a far-future `exp` combined with `jwt_expire_at`'s 5s skew to extend usable lifetime, so that jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI is violated, leading to authentication bypass via forged JWT claims? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically a token with a far-future `exp` combined with `jwt_expire_at`'s 5s skew to extend usable lifetime.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI
- Expected Immunefi impact: authentication bypass via forged JWT claims (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
