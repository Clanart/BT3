# Q0413: receive: A topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_...

## Question
Can an unprivileged attacker (the `:type` route segment, the raw body, and all webhook headers) reach `receive` in app/controllers/shopify_app/webhooks_controller.rb via POST /webhooks/:type, supplying a topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_DOMAIN` is attacker-set and trusted downstream, so that no webhook side effect may run before HMAC verification succeeds over the raw body is violated, leading to forged webhook processing (unauthorized state change)? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: app/controllers/shopify_app/webhooks_controller.rb — `receive`
- Entrypoint: POST /webhooks/:type
- Attacker controls: the `:type` route segment, the raw body, and all webhook headers — specifically a topic/shop-domain header mismatch where `HTTP_X_SHOPIFY_SHOP_DOMAIN` is attacker-set and trusted downstream.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: no webhook side effect may run before HMAC verification succeeds over the raw body
- Expected Immunefi impact: forged webhook processing (unauthorized state change) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
