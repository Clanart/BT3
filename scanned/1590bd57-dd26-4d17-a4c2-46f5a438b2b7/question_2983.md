# Q2983: safe_embedded_app_url: A `host` with an embedded `@` that `embedded_host_authority` mu...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a `host` with an embedded `@` that `embedded_host_authority` must catch but a crafted `/`/`?` boundary hides, so that the decoded embedded host must resolve to a Shopify admin origin for the acting shop only is violated, leading to open redirect to attacker origin carrying id_token/host (token exfiltration)? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a `host` with an embedded `@` that `embedded_host_authority` must catch but a crafted `/`/`?` boundary hides.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: the decoded embedded host must resolve to a Shopify admin origin for the acting shop only
- Expected Immunefi impact: open redirect to attacker origin carrying id_token/host (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
