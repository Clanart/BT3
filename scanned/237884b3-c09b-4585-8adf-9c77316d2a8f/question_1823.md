# Q1823: verify_request: A body whose HMAC is computed over different bytes than the ext...

## Question
Can an unprivileged attacker (the raw body and X-Shopify-Hmac-SHA256 header) reach `verify_request` in app/controllers/shopify_app/extension_verification_controller.rb via POST to any controller subclassing ExtensionVerificationController, supplying a body whose HMAC is computed over different bytes than the extension action later reads, so that extension requests must be rejected before any action body runs when HMAC is invalid is violated, leading to forged extension request accepted (auth bypass)? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: app/controllers/shopify_app/extension_verification_controller.rb — `verify_request`
- Entrypoint: POST to any controller subclassing ExtensionVerificationController
- Attacker controls: the raw body and X-Shopify-Hmac-SHA256 header — specifically a body whose HMAC is computed over different bytes than the extension action later reads.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: extension requests must be rejected before any action body runs when HMAC is invalid
- Expected Immunefi impact: forged extension request accepted (auth bypass) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
