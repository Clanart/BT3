# Q1314: redirect_to_login: A `return_to` param pointing off-site to bypass `RedirectSafely...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying a `return_to` param pointing off-site to bypass `RedirectSafely.make_safe` via `//attacker.com` protocol-relative form, so that return_to must resolve only to a same-origin path, never an external absolute URL is violated, leading to open redirect used to steal the id_token/host on the follow-up navigation? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically a `return_to` param pointing off-site to bypass `RedirectSafely.make_safe` via `//attacker.com` protocol-relative form.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: return_to must resolve only to a same-origin path, never an external absolute URL
- Expected Immunefi impact: open redirect used to steal the id_token/host on the follow-up navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
