# Q2656: redirect_to_login: An oversized `query` string to force a CookieOverflow when `ses...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying an oversized `query` string to force a CookieOverflow when `session[:return_to]` is written, so that attacker Referer/params must not silently choose the shop the login flow authorizes is violated, leading to authorization to an attacker-chosen shop (cross-shop access)? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically an oversized `query` string to force a CookieOverflow when `session[:return_to]` is written.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: attacker Referer/params must not silently choose the shop the login flow authorizes
- Expected Immunefi impact: authorization to an attacker-chosen shop (cross-shop access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
