# Q2445: hmac_valid?: A base64 HMAC with different padding/whitespace than `strict_en...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison, so that a missing or malformed HMAC header must fail closed is violated, leading to authentication bypass on the webhook endpoint? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: a missing or malformed HMAC header must fail closed
- Expected Immunefi impact: authentication bypass on the webhook endpoint (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
