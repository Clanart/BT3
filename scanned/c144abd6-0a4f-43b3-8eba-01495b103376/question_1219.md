# Q1219: respond_to_invalid_shopify_id_token: Query params that flow into `patch_shopify_id_token_params` and...

## Question
Can an unprivileged attacker (the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param) reach `respond_to_invalid_shopify_id_token / redirect_to_bounce_page` in lib/shopify_app/controller_concerns/token_exchange.rb via an embedded GET with an invalid/missing id_token that triggers the bounce redirect, supplying query params that flow into `patch_shopify_id_token_params` and the `shopify-reload` bounce URL unvalidated, so that the bounce/reload target must stay on the app origin and carry no attacker absolute URL is violated, leading to open redirect carrying a fresh session token to an attacker origin? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `respond_to_invalid_shopify_id_token / redirect_to_bounce_page`
- Entrypoint: an embedded GET with an invalid/missing id_token that triggers the bounce redirect
- Attacker controls: the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param — specifically query params that flow into `patch_shopify_id_token_params` and the `shopify-reload` bounce URL unvalidated.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the bounce/reload target must stay on the app origin and carry no attacker absolute URL
- Expected Immunefi impact: open redirect carrying a fresh session token to an attacker origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
