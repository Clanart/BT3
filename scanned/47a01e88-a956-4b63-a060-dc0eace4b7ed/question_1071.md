# Q1071: callback: A callback whose `state` does not match the OAuth cookie, probi...

## Question
Can an unprivileged attacker (`code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie) reach `callback / validated_auth_objects` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback (public OAuth redirect target), supplying a callback whose `state` does not match the OAuth cookie, probing CSRF/state binding in validate_auth_callback, so that callback state/hmac must be validated before any session persistence is violated, leading to authentication bypass / cross-shop token storage? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `callback / validated_auth_objects`
- Entrypoint: GET /auth/shopify/callback (public OAuth redirect target)
- Attacker controls: `code`, `shop`, `state`, `host`, `hmac`, `timestamp` and the OAuth cookie — specifically a callback whose `state` does not match the OAuth cookie, probing CSRF/state binding in validate_auth_callback.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: callback state/hmac must be validated before any session persistence
- Expected Immunefi impact: authentication bypass / cross-shop token storage (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
