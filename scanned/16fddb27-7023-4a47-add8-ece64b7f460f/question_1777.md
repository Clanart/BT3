# Q1777: shopify_id_token: An unsigned/`alg:none` style token to see whether `JwtPayload.n...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying an unsigned/`alg:none` style token to see whether `JwtPayload.new` is trusted without signature verification here, so that jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI is violated, leading to authentication bypass via forged JWT claims? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically an unsigned/`alg:none` style token to see whether `JwtPayload.new` is trusted without signature verification here.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI
- Expected Immunefi impact: authentication bypass via forged JWT claims (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
