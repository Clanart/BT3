# Q0484: verify_request: A request ordering issue where `head(:unauthorized)` is set but...

## Question
Can an unprivileged attacker (the raw body and X-Shopify-Hmac-SHA256 header) reach `verify_request` in app/controllers/shopify_app/extension_verification_controller.rb via POST to any controller subclassing ExtensionVerificationController, supplying a request ordering issue where `head(:unauthorized)` is set but execution continues (note the head-before-log ordering), so that extension requests must be rejected before any action body runs when HMAC is invalid is violated, leading to forged extension request accepted (auth bypass)? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: app/controllers/shopify_app/extension_verification_controller.rb — `verify_request`
- Entrypoint: POST to any controller subclassing ExtensionVerificationController
- Attacker controls: the raw body and X-Shopify-Hmac-SHA256 header — specifically a request ordering issue where `head(:unauthorized)` is set but execution continues (note the head-before-log ordering).
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: extension requests must be rejected before any action body runs when HMAC is invalid
- Expected Immunefi impact: forged extension request accepted (auth bypass) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
