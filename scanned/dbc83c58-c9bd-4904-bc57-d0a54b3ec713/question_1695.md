# Q1695: receive: A topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_DOMAIN` is attacker-set and trusted downstream, so that the shop-domain header must not be trusted as identity without HMAC binding is violated, leading to cross-shop data spoofing via forged webhook headers? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_DOMAIN` is attacker-set and trusted downstream.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: the shop-domain header must not be trusted as identity without HMAC binding
- Expected Immunefi impact: cross-shop data spoofing via forged webhook headers (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
