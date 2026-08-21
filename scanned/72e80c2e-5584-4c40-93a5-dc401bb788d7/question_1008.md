# Q1008: redirect_to_splash_page: A `current_shopify_domain` raising ShopifyDomainNotFound to fal...

## Question
Can an unprivileged attacker (`host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt) reach `redirect_to_splash_page / splash_page / missing_expected_jwt?` in app/controllers/concerns/shopify_app/ensure_authenticated_links.rb via GET a link-authenticated controller action without a JWT, supplying a `current_shopify_domain` raising ShopifyDomainNotFound to fall back to login_url (probe param leakage), so that the splash redirect must stay on the app origin with trusted params only is violated, leading to open redirect / parameter leak on the splash bounce? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_authenticated_links.rb — `redirect_to_splash_page / splash_page / missing_expected_jwt?`
- Entrypoint: GET a link-authenticated controller action without a JWT
- Attacker controls: `host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt — specifically a `current_shopify_domain` raising ShopifyDomainNotFound to fall back to login_url (probe param leakage).
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: the splash redirect must stay on the app origin with trusted params only
- Expected Immunefi impact: open redirect / parameter leak on the splash bounce (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
