# Q0777: hmac_valid?: A base64 HMAC with different padding/whitespace than `strict_en...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison, so that a missing or malformed HMAC header must fail closed is violated, leading to authentication bypass on the webhook endpoint? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: a missing or malformed HMAC header must fail closed
- Expected Immunefi impact: authentication bypass on the webhook endpoint (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
