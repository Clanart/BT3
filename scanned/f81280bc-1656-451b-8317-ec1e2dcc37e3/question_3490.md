# Q3490: hmac_valid?: An attempt to exploit `old_secret` acceptance to forge with a r...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying an attempt to exploit `old_secret` acceptance to forge with a rotated-out secret still configured, so that only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes is violated, leading to forged webhook accepted -> unauthorized state change / spoofed shop data? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically an attempt to exploit `old_secret` acceptance to forge with a rotated-out secret still configured.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes
- Expected Immunefi impact: forged webhook accepted -> unauthorized state change / spoofed shop data (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
