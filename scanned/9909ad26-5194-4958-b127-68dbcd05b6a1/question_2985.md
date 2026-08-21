# Q2985: hmac_valid?: A base64 HMAC with different padding/whitespace than `strict_en...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison, so that only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes is violated, leading to forged webhook accepted -> unauthorized state change / spoofed shop data? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a base64 HMAC with different padding/whitespace than `strict_encode64` output to probe the comparison.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes
- Expected Immunefi impact: forged webhook accepted -> unauthorized state change / spoofed shop data (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
