# Q2787: query_string_valid?: Duplicate keys like `a=1&a=2` where `Rack::Utils.parse_query` a...

## Question
Can an unprivileged attacker (the entire query string including `signature` and repeated/array params) reach `query_string_valid? / calculated_signature` in lib/shopify_app/controller_concerns/app_proxy_verification.rb via GET/POST an app-proxy-protected controller action, supplying duplicate keys like `a=1&a=2` where `Rack::Utils.parse_query` and `Array(v).join(',')` reassemble differently than Shopify signs, so that the reconstructed canonical string must exactly equal what Shopify signed, for any param shape is violated, leading to forged app-proxy request accepted (auth bypass / acting as a signed Shopify request)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/controller_concerns/app_proxy_verification.rb — `query_string_valid? / calculated_signature`
- Entrypoint: GET/POST an app-proxy-protected controller action
- Attacker controls: the entire query string including `signature` and repeated/array params — specifically duplicate keys like `a=1&a=2` where `Rack::Utils.parse_query` and `Array(v).join(',')` reassemble differently than Shopify signs.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: the reconstructed canonical string must exactly equal what Shopify signed, for any param shape
- Expected Immunefi impact: forged app-proxy request accepted (auth bypass / acting as a signed Shopify request) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
