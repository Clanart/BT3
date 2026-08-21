# Q2284: respond_to_invalid_shopify_id_token: ShopifyAPI::Context.host concatenated with attacker path segmen...

## Question
Can an unprivileged attacker (the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param) reach `respond_to_invalid_shopify_id_token / redirect_to_bounce_page` in lib/shopify_app/controller_concerns/token_exchange.rb via an embedded GET with an invalid/missing id_token that triggers the bounce redirect, supplying ShopifyAPI::Context.host concatenated with attacker path segments in the bounce URL, so that the bounce/reload target must stay on the app origin and carry no attacker absolute URL is violated, leading to open redirect carrying a fresh session token to an attacker origin? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `respond_to_invalid_shopify_id_token / redirect_to_bounce_page`
- Entrypoint: an embedded GET with an invalid/missing id_token that triggers the bounce redirect
- Attacker controls: the full query string (minus id_token), `shop`, `host`, and the `shopify-reload` param — specifically ShopifyAPI::Context.host concatenated with attacker path segments in the bounce URL.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: the bounce/reload target must stay on the app origin and carry no attacker absolute URL
- Expected Immunefi impact: open redirect carrying a fresh session token to an attacker origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
