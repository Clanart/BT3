# Q2987: safe_embedded_app_url: A `host` containing control chars 0x00-0x20 or 0x7f or backslas...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a `host` containing control chars 0x00-0x20 or 0x7f or backslash probing `unsafe_embedded_host_characters?`, so that the decoded embedded host must resolve to a Shopify admin origin for the acting shop only is violated, leading to open redirect to attacker origin carrying id_token/host (token exfiltration)? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a `host` containing control chars 0x00-0x20 or 0x7f or backslash probing `unsafe_embedded_host_characters?`.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: the decoded embedded host must resolve to a Shopify admin origin for the acting shop only
- Expected Immunefi impact: open redirect to attacker origin carrying id_token/host (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
