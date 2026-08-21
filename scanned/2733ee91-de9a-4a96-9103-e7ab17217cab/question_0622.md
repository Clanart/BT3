# Q0622: shopify_id_token: A JWT whose `shopify_domain` claim is an untrusted host, checki...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying a JWT whose `shopify_domain` claim is an untrusted host, checking `sanitize_shop_domain` gating in `jwt_shopify_domain`, so that choosing header vs param must not let an attacker supply a second, weaker token is violated, leading to session confusion / auth bypass? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically a JWT whose `shopify_domain` claim is an untrusted host, checking `sanitize_shop_domain` gating in `jwt_shopify_domain`.
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: choosing header vs param must not let an attacker supply a second, weaker token
- Expected Immunefi impact: session confusion / auth bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
