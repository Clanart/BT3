# Q0039: respond_to_invalid_shopify_id_token: A request that is embedded=1 vs not, steering between `redirect...

## Question
Can an unprivileged attacker (the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param) reach `respond_to_invalid_shopify_id_token / redirect_to_bounce_page` in lib/shopify_app/controller_concerns/token_exchange.rb via an embedded GET with an invalid/missing id_token that triggers the bounce redirect, supplying a request that is embedded=1 vs not, steering between `redirect_to_bounce_page` and `redirect_to_embed_app_in_admin`, so that the bounce/reload target must stay on the app origin and carry no attacker absolute URL is violated, leading to open redirect carrying a fresh session token to an attacker origin? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `respond_to_invalid_shopify_id_token / redirect_to_bounce_page`
- Entrypoint: an embedded GET with an invalid/missing id_token that triggers the bounce redirect
- Attacker controls: the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param — specifically a request that is embedded=1 vs not, steering between `redirect_to_bounce_page` and `redirect_to_embed_app_in_admin`.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: the bounce/reload target must stay on the app origin and carry no attacker absolute URL
- Expected Immunefi impact: open redirect carrying a fresh session token to an attacker origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
