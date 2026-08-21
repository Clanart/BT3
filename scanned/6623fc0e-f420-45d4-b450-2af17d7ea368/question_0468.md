# Q0468: shopify_id_token: A token present but empty string, so `jwt_payload` becomes nil ...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying a token present but empty string, so `jwt_payload` becomes nil and downstream treats missing-as-valid, so that jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI is violated, leading to authentication bypass via forged JWT claims? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically a token present but empty string, so `jwt_payload` becomes nil and downstream treats missing-as-valid.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI
- Expected Immunefi impact: authentication bypass via forged JWT claims (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
