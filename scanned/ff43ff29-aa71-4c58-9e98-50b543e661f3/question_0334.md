# Q0334: receive: A topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_DOMAIN` is attacker-set and trusted downstream, so that the shop-domain header must not be trusted as identity without HMAC binding is violated, leading to cross-shop data spoofing via forged webhook headers? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_DOMAIN` is attacker-set and trusted downstream.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the shop-domain header must not be trusted as identity without HMAC binding
- Expected Immunefi impact: cross-shop data spoofing via forged webhook headers (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
