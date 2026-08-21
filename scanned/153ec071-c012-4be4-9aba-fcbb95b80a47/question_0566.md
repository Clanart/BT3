# Q0566: redirect_uri_for_embedded: An attacker Referer feeding `referer_sanitized_shop_name` into ...

## Question
Can an unprivileged attacker (`shop`, `host`, `embedded`, `redirect_uri`, and all other query params (echoed via sanitized_params)) reach `redirect_uri_for_embedded / add_app_bridge_redirect_url_header` in lib/shopify_app/controller_concerns/redirect_for_embedded.rb via GET an EnsureInstalled action with embedded=1 and no known shop session, supplying an attacker Referer feeding `referer_sanitized_shop_name` into the redirect shop, so that the composed embedded redirect must target the app's own login on a trusted origin is violated, leading to open redirect leaking OAuth/session params to attacker origin? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: lib/shopify_app/controller_concerns/redirect_for_embedded.rb — `redirect_uri_for_embedded / add_app_bridge_redirect_url_header`
- Entrypoint: GET an EnsureInstalled action with embedded=1 and no known shop session
- Attacker controls: `shop`, `host`, `embedded`, `redirect_uri`, and all other query params (echoed via sanitized_params) — specifically an attacker Referer feeding `referer_sanitized_shop_name` into the redirect shop.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: the composed embedded redirect must target the app's own login on a trusted origin
- Expected Immunefi impact: open redirect leaking OAuth/session params to attacker origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
