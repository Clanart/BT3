# Q1342: redirect_to_login: A `host` param reflected unvalidated into `login_url_params[:ho...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying a `host` param reflected unvalidated into `login_url_params[:host]`, so that attacker Referer/params must not silently choose the shop the login flow authorizes is violated, leading to authorization to an attacker-chosen shop (cross-shop access)? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically a `host` param reflected unvalidated into `login_url_params[:host]`.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: attacker Referer/params must not silently choose the shop the login flow authorizes
- Expected Immunefi impact: authorization to an attacker-chosen shop (cross-shop access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
