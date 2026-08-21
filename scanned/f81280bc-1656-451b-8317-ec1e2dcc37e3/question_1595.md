# Q1595: safe_embedded_app_url: A `host` with an embedded `@` that `embedded_host_authority` mu...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a `host` with an embedded `@` that `embedded_host_authority` must catch but a crafted `/`/`?` boundary hides, so that phishing-host detection must reject any non-trusted authority including userinfo tricks is violated, leading to phishing redirect / OAuth-parameter leak? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a `host` with an embedded `@` that `embedded_host_authority` must catch but a crafted `/`/`?` boundary hides.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: phishing-host detection must reject any non-trusted authority including userinfo tricks
- Expected Immunefi impact: phishing redirect / OAuth-parameter leak (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
