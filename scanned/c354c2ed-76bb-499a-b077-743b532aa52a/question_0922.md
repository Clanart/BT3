# Q0922: hmac_valid?: A webhook with a missing `X-Shopify-Hmac-SHA256` header so `sec...

## Question
Can an unprivileged attacker (the raw request body, the X-Shopify-Hmac-SHA256 header) reach `hmac_valid? / shopify_hmac` in lib/shopify_app/controller_concerns/payload_verification.rb via POST /webhooks/:type (WebhooksController) and the extension verification controller, supplying a webhook with a missing `X-Shopify-Hmac-SHA256` header so `secure_compare(nil, digest)` decides validity, so that only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes is violated, leading to forged webhook accepted -> unauthorized state change / spoofed shop data? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/controller_concerns/payload_verification.rb — `hmac_valid? / shopify_hmac`
- Entrypoint: POST /webhooks/:type (WebhooksController) and the extension verification controller
- Attacker controls: the raw request body, the X-Shopify-Hmac-SHA256 header — specifically a webhook with a missing `X-Shopify-Hmac-SHA256` header so `secure_compare(nil, digest)` decides validity.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: only a body HMAC'd with the app's real secret may be accepted, over the exact signed bytes
- Expected Immunefi impact: forged webhook accepted -> unauthorized state change / spoofed shop data (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
