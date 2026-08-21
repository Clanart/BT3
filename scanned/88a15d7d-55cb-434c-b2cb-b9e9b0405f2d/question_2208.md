# Q2208: safe_embedded_app_url: A `host` containing control chars 0x00-0x20 or 0x7f or backslas...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a `host` containing control chars 0x00-0x20 or 0x7f or backslash probing `unsafe_embedded_host_characters?`, so that phishing-host detection must reject any non-trusted authority including userinfo tricks is violated, leading to phishing redirect / OAuth-parameter leak? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a `host` containing control chars 0x00-0x20 or 0x7f or backslash probing `unsafe_embedded_host_characters?`.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: phishing-host detection must reject any non-trusted authority including userinfo tricks
- Expected Immunefi impact: phishing redirect / OAuth-parameter leak (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
