# Q1265: redirect_to_splash_page: A request with no JWT so `missing_expected_jwt?` triggers a spl...

## Question
Can an unprivileged attacker (`host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt) reach `redirect_to_splash_page / splash_page / missing_expected_jwt?` in app/controllers/concerns/shopify_app/ensure_authenticated_links.rb via GET a link-authenticated controller action without a JWT, supplying a request with no JWT so `missing_expected_jwt?` triggers a splash redirect carrying attacker `host`/`embedded`, so that the splash redirect must stay on the app origin with trusted params only is violated, leading to open redirect / parameter leak on the splash bounce? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: app/controllers/concerns/shopify_app/ensure_authenticated_links.rb — `redirect_to_splash_page / splash_page / missing_expected_jwt?`
- Entrypoint: GET a link-authenticated controller action without a JWT
- Attacker controls: `host`, `embedded`, `return_to` (request.fullpath), and the `shop` derived from jwt — specifically a request with no JWT so `missing_expected_jwt?` triggers a splash redirect carrying attacker `host`/`embedded`.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: the splash redirect must stay on the app origin with trusted params only
- Expected Immunefi impact: open redirect / parameter leak on the splash bounce (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
