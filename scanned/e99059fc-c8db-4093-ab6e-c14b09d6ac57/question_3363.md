# Q3363: hmac_valid?: A very large body to see whether verification is skipped or tru...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a very large body to see whether verification is skipped or truncated before `secure_compare`, so that a missing or malformed HMAC header must fail closed is violated, leading to authentication bypass on the webhook endpoint? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a very large body to see whether verification is skipped or truncated before `secure_compare`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: a missing or malformed HMAC header must fail closed
- Expected Immunefi impact: authentication bypass on the webhook endpoint (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
