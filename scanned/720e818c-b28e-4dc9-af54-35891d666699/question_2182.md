# Q2182: safe_embedded_app_url: A base64 `host` decoding to `admin.shopify.com/store/x@attacker...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a base64 `host` decoding to `admin.shopify.com/store/x@attacker.com` combining userinfo and unified-admin paths, so that the decoded embedded host must resolve to a Shopify admin origin for the acting shop only is violated, leading to open redirect to attacker origin carrying id_token/host (token exfiltration)? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a base64 `host` decoding to `admin.shopify.com/store/x@attacker.com` combining userinfo and unified-admin paths.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: the decoded embedded host must resolve to a Shopify admin origin for the acting shop only
- Expected Immunefi impact: open redirect to attacker origin carrying id_token/host (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
