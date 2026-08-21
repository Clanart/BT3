# Q0719: respond_to_invalid_shopify_id_token: Query params that flow into `patch_shopify_id_token_params` and...

## Question
Can an unprivileged attacker (the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param) reach `respond_to_invalid_shopify_id_token / redirect_to_bounce_page` in lib/shopify_app/controller_concerns/token_exchange.rb via an embedded GET with an invalid/missing id_token that triggers the bounce redirect, supplying query params that flow into `patch_shopify_id_token_params` and the `shopify-reload` bounce URL unvalidated, so that the bounce/reload target must stay on the app origin and carry no attacker absolute URL is violated, leading to open redirect carrying a fresh session token to an attacker origin? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `respond_to_invalid_shopify_id_token / redirect_to_bounce_page`
- Entrypoint: an embedded GET with an invalid/missing id_token that triggers the bounce redirect
- Attacker controls: the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param — specifically query params that flow into `patch_shopify_id_token_params` and the `shopify-reload` bounce URL unvalidated.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: the bounce/reload target must stay on the app origin and carry no attacker absolute URL
- Expected Immunefi impact: open redirect carrying a fresh session token to an attacker origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
