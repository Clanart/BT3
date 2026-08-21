# Q1597: verify_request: A request ordering issue where `head(:unauthorized)` is set but...

## Question
Can an unprivileged attacker (the raw body and X-Shopify-Hmac-SHA256 header) reach `verify_request` in app/controllers/shopify_app/extension_verification_controller.rb via POST to any controller subclassing ExtensionVerificationController, supplying a request ordering issue where `head(:unauthorized)` is set but execution continues (note the head-before-log ordering), so that extension requests must be rejected before any action body runs when HMAC is invalid is violated, leading to forged extension request accepted (auth bypass)? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: app/controllers/shopify_app/extension_verification_controller.rb — `verify_request`
- Entrypoint: POST to any controller subclassing ExtensionVerificationController
- Attacker controls: the raw body and X-Shopify-Hmac-SHA256 header — specifically a request ordering issue where `head(:unauthorized)` is set but execution continues (note the head-before-log ordering).
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: extension requests must be rejected before any action body runs when HMAC is invalid
- Expected Immunefi impact: forged extension request accepted (auth bypass) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
