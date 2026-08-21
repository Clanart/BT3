# Q1204: redirect_to_splash_page: A `base_url` from `root_url` concatenated with attacker query p...

## Question
Can an unprivileged attacker (`host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt) reach `redirect_to_splash_page / splash_page / missing_expected_jwt?` in app/controllers/concerns/shopify_app/ensure_authenticated_links.rb via GET a link-authenticated controller action without a JWT, supplying a `base_url` from `root_url` concatenated with attacker query producing an unexpected external target, so that the splash redirect must stay on the app origin with trusted params only is violated, leading to open redirect / parameter leak on the splash bounce? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_authenticated_links.rb — `redirect_to_splash_page / splash_page / missing_expected_jwt?`
- Entrypoint: GET a link-authenticated controller action without a JWT
- Attacker controls: `host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt — specifically a `base_url` from `root_url` concatenated with attacker query producing an unexpected external target.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: the splash redirect must stay on the app origin with trusted params only
- Expected Immunefi impact: open redirect / parameter leak on the splash bounce (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
