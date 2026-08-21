# Q3543: redirect_to_login: An `access_scopes` array param that is joined into `scope=` and...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying an `access_scopes` array param that is joined into `scope=` and reflected into the login URL, so that return_to must resolve only to a same-origin path, never an external absolute URL is violated, leading to open redirect used to steal the id_token/host on the follow-up navigation? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically an `access_scopes` array param that is joined into `scope=` and reflected into the login URL.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: return_to must resolve only to a same-origin path, never an external absolute URL
- Expected Immunefi impact: open redirect used to steal the id_token/host on the follow-up navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
