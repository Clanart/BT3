# Q2014: redirect_to_login: A `return_to` with a backslash `/\attacker.com` or whitespace t...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying a `return_to` with a backslash `/\attacker.com` or whitespace that make_safe may normalize differently than the browser, so that return_to must resolve only to a same-origin path, never an external absolute URL is violated, leading to open redirect used to steal the id_token/host on the follow-up navigation? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically a `return_to` with a backslash `/\attacker.com` or whitespace that make_safe may normalize differently than the browser.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: return_to must resolve only to a same-origin path, never an external absolute URL
- Expected Immunefi impact: open redirect used to steal the id_token/host on the follow-up navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
