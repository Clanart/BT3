# Q2371: hmac_valid?: A base64 HMAC with different padding/whitespace than `strict_en...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison, so that a missing or malformed HMAC header must fail closed is violated, leading to authentication bypass on the webhook endpoint? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: a missing or malformed HMAC header must fail closed
- Expected Immunefi impact: authentication bypass on the webhook endpoint (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
