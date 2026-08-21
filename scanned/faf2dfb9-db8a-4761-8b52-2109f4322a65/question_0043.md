# Q0043: redirect_to_splash_page: A `current_shopify_domain` raising ShopifyDomainNotFound to fal...

## Question
Can an unprivileged attacker (`host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt) reach `redirect_to_splash_page / splash_page / missing_expected_jwt?` in app/controllers/concerns/shopify_app/ensure_authenticated_links.rb via GET a link-authenticated controller action without a JWT, supplying a `current_shopify_domain` raising ShopifyDomainNotFound to fall back to login_url (probe param leakage), so that the splash redirect must stay on the app origin with trusted params only is violated, leading to open redirect / parameter leak on the splash bounce? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_authenticated_links.rb — `redirect_to_splash_page / splash_page / missing_expected_jwt?`
- Entrypoint: GET a link-authenticated controller action without a JWT
- Attacker controls: `host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt — specifically a `current_shopify_domain` raising ShopifyDomainNotFound to fall back to login_url (probe param leakage).
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: the splash redirect must stay on the app origin with trusted params only
- Expected Immunefi impact: open redirect / parameter leak on the splash bounce (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
