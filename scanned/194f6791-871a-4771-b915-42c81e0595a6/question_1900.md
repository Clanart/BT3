# Q1900: hmac_valid?: A body whose raw bytes differ from the parsed params so the sig...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a body whose raw bytes differ from the parsed params so the signed bytes and processed data disagree, so that a missing or malformed HMAC header must fail closed is violated, leading to authentication bypass on the webhook endpoint? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a body whose raw bytes differ from the parsed params so the signed bytes and processed data disagree.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: a missing or malformed HMAC header must fail closed
- Expected Immunefi impact: authentication bypass on the webhook endpoint (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
