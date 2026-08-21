# Q3309: shopify_id_token: A token present but empty string, so `jwt_payload` becomes nil ...

## Question
Can an unprivileged attacker (the Authorization header, the `id_token` URL param, and the JWT body) reach `shopify_id_token / jwt_payload / jwt_shopify_domain` in lib/shopify_app/controller_concerns/with_shopify_id_token.rb via any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links), supplying a token present but empty string, so `jwt_payload` becomes nil and downstream treats missing-as-valid, so that jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI is violated, leading to authentication bypass via forged JWT claims? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/controller_concerns/with_shopify_id_token.rb — `shopify_id_token / jwt_payload / jwt_shopify_domain`
- Entrypoint: any request to a controller including WithShopifyIdToken (auth, CSRF, ensure_authenticated_links)
- Attacker controls: the Authorization header, the `id_token` URL param, and the JWT body — specifically a token present but empty string, so `jwt_payload` becomes nil and downstream treats missing-as-valid.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: jwt claims must be treated as untrusted until signature/aud/dest are verified by ShopifyAPI
- Expected Immunefi impact: authentication bypass via forged JWT claims (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
