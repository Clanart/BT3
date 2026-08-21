# Q0040: safe_embedded_app_url: A `host` with an embedded `@` that `embedded_host_authority` mu...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a `host` with an embedded `@` that `embedded_host_authority` must catch but a crafted `/`/`?` boundary hides, so that phishing-host detection must reject any non-trusted authority including userinfo tricks is violated, leading to phishing redirect / OAuth-parameter leak? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a `host` with an embedded `@` that `embedded_host_authority` must catch but a crafted `/`/`?` boundary hides.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: phishing-host detection must reject any non-trusted authority including userinfo tricks
- Expected Immunefi impact: phishing redirect / OAuth-parameter leak (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
