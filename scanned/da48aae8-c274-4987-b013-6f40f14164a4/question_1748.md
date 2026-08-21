# Q1748: redirect_to_login: A crafted Referer header so `referer_sanitized_shop_name` injec...

## Question
Can an unprivileged attacker (`return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params) reach `redirect_to_login / return_to_url / login_url_params` in lib/shopify_app/controller_concerns/login_protection.rb via GET a protected action while unauthenticated (triggers redirect_to_login), supplying a crafted Referer header so `referer_sanitized_shop_name` injects an attacker shop into `login_url_params[:shop]`, so that attacker Referer/params must not silently choose the shop the login flow authorizes is violated, leading to authorization to an attacker-chosen shop (cross-shop access)? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `redirect_to_login / return_to_url / login_url_params`
- Entrypoint: GET a protected action while unauthenticated (triggers redirect_to_login)
- Attacker controls: `return_to`, `shop`, `host`, the Referer header, and arbitrary extra query params — specifically a crafted Referer header so `referer_sanitized_shop_name` injects an attacker shop into `login_url_params[:shop]`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: attacker Referer/params must not silently choose the shop the login flow authorizes
- Expected Immunefi impact: authorization to an attacker-chosen shop (cross-shop access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
