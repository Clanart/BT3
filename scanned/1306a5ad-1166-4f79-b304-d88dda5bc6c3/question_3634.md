# Q3634: shopify_id_token: An unsigned/`alg:none` style token to see whether `JwtPayload.n...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying an unsigned/`alg:none` style token to see whether `JwtPayload.new` is trusted without signature verification here, so that choosing header vs param must not let an attacker supply a second, weaker token is violated, leading to session confusion / auth bypass? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically an unsigned/`alg:none` style token to see whether `JwtPayload.new` is trusted without signature verification here.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: choosing header vs param must not let an attacker supply a second, weaker token
- Expected Immunefi impact: session confusion / auth bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
