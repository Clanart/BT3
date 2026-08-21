# Q3875: redirect_uri_for_embedded: An `embedded_redirect_url` config route request that loops or i...

## Question
Can an unprivileged attacker (`shop`, `host`, `embedded`, `redirect_uri`, and all other query params (echoed via sanitized_params)) reach `redirect_uri_for_embedded / add_app_bridge_redirect_url_header` in lib/shopify_app/controller_concerns/redirect_for_embedded.rb via GET an EnsureInstalled action with embedded=1 and no known shop session, supplying an `embedded_redirect_url` config route request that loops or is used to bounce to an external URL, so that the composed embedded redirect must target the app's own login on a trusted origin is violated, leading to open redirect leaking OAuth/session params to attacker origin? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/redirect_for_embedded.rb — `redirect_uri_for_embedded / add_app_bridge_redirect_url_header`
- Entrypoint: GET an EnsureInstalled action with embedded=1 and no known shop session
- Attacker controls: `shop`, `host`, `embedded`, `redirect_uri`, and all other query params (echoed via sanitized_params) — specifically an `embedded_redirect_url` config route request that loops or is used to bounce to an external URL.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: the composed embedded redirect must target the app's own login on a trusted origin
- Expected Immunefi impact: open redirect leaking OAuth/session params to attacker origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
