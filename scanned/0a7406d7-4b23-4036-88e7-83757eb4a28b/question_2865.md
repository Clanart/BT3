# Q2865: safe_embedded_app_url: No `host` but a `shop`, so `Base64.encode64("#{sanitized_shop_n...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying no `host` but a `shop`, so `Base64.encode64("#{sanitized_shop_name}/admin")` builds the host from the shop param, so that the decoded embedded host must resolve to a Shopify admin origin for the acting shop only is violated, leading to open redirect to attacker origin carrying id_token/host (token exfiltration)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically no `host` but a `shop`, so `Base64.encode64("#{sanitized_shop_name}/admin")` builds the host from the shop param.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: the decoded embedded host must resolve to a Shopify admin origin for the acting shop only
- Expected Immunefi impact: open redirect to attacker origin carrying id_token/host (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
