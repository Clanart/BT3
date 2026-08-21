# Q0490: redirect_to_login: An oversized `query` string to force a CookieOverflow when `ses...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying an oversized `query` string to force a CookieOverflow when `session[:return_to]` is written, so that return_to must resolve only to a same-origin path, never an external absolute URL is violated, leading to open redirect used to steal the id_token/host on the follow-up navigation? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically an oversized `query` string to force a CookieOverflow when `session[:return_to]` is written.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: return_to must resolve only to a same-origin path, never an external absolute URL
- Expected Immunefi impact: open redirect used to steal the id_token/host on the follow-up navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
