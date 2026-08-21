# Q3863: hmac_valid?: A request replaying a previously-captured valid webhook body+HM...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a request replaying a previously-captured valid webhook body+HMAC to re-trigger processing, so that only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes is violated, leading to forged webhook accepted -> unauthorized state change / spoofed shop data? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a request replaying a previously-captured valid webhook body+HMAC to re-trigger processing.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes
- Expected Immunefi impact: forged webhook accepted -> unauthorized state change / spoofed shop data (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
