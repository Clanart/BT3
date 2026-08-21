# Q0002: verify_request: A request ordering issue where `head(:unauthorized)` is set but...

## Question
Can an unprivileged attacker (the raw body and X-Shopify-Hmac-SHA256 header) reach `verify_request` in app/controllers/shopify_app/extension_verification_controller.rb via POST to any controller subclassing ExtensionVerificationController, supplying a request ordering issue where `head(:unauthorized)` is set but execution continues (note the head-before-log ordering), so that extension requests must be rejected before any action body runs when HMAC is invalid is violated, leading to forged extension request accepted (auth bypass)? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: app/controllers/shopify_app/extension_verification_controller.rb — `verify_request`
- Entrypoint: POST to any controller subclassing ExtensionVerificationController
- Attacker controls: the raw body and X-Shopify-Hmac-SHA256 header — specifically a request ordering issue where `head(:unauthorized)` is set but execution continues (note the head-before-log ordering).
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: extension requests must be rejected before any action body runs when HMAC is invalid
- Expected Immunefi impact: forged extension request accepted (auth bypass) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
