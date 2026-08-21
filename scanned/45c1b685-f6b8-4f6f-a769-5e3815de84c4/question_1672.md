# Q1672: hmac_valid?: A very large body to see whether verification is skipped or tru...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a very large body to see whether verification is skipped or truncated before `secure_compare`, so that only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes is violated, leading to forged webhook accepted -> unauthorized state change / spoofed shop data? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a very large body to see whether verification is skipped or truncated before `secure_compare`.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes
- Expected Immunefi impact: forged webhook accepted -> unauthorized state change / spoofed shop data (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
