# Q0032: verify_request: A body whose HMAC is computed over different bytes than the ext...

## Question
Can an unprivileged attacker (the raw body and X-Shopify-Hmac-SHA256 header) reach `verify_request` in app/controllers/shopify_app/extension_verification_controller.rb via POST to any controller subclassing ExtensionVerificationController, supplying a body whose HMAC is computed over different bytes than the extension action later reads, so that extension requests must be rejected before any action body runs when HMAC is invalid is violated, leading to forged extension request accepted (auth bypass)? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: app/controllers/shopify_app/extension_verification_controller.rb — `verify_request`
- Entrypoint: POST to any controller subclassing ExtensionVerificationController
- Attacker controls: the raw body and X-Shopify-Hmac-SHA256 header — specifically a body whose HMAC is computed over different bytes than the extension action later reads.
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: extension requests must be rejected before any action body runs when HMAC is invalid
- Expected Immunefi impact: forged extension request accepted (auth bypass) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
