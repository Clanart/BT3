# Q1916: shopify_id_token: A token with a far-future `exp` combined with `jwt_expire_at`'s...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying a token with a far-future `exp` combined with `jwt_expire_at`'s 5s skew to extend usable lifetime, so that choosing header vs param must not let an attacker supply a second, weaker token is violated, leading to session confusion / auth bypass? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically a token with a far-future `exp` combined with `jwt_expire_at`'s 5s skew to extend usable lifetime.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: choosing header vs param must not let an attacker supply a second, weaker token
- Expected Immunefi impact: session confusion / auth bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
