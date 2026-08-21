# Q1806: query_string_valid?: Duplicate keys like `a=1&a=2` where `Rack::Utils.parse_query` a...

## Question
Can an unprivileged attacker (the entire query string including `signature` and repeated/array params) reach `query_string_valid? / calculated_signature` in lib/shopify_app/controller_concerns/app_proxy_verification.rb via GET/POST an app-proxy-protected controller action, supplying duplicate keys like `a=1&a=2` where `Rack::Utils.parse_query` and `Array(v).join(',')` reassemble differently than Shopify signs, so that the reconstructed canonical string must exactly equal what Shopify signed, for any param shape is violated, leading to forged app-proxy request accepted (auth bypass / acting as a signed Shopify request)? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/app_proxy_verification.rb — `query_string_valid? / calculated_signature`
- Entrypoint: GET/POST an app-proxy-protected controller action
- Attacker controls: the entire query string including `signature` and repeated/array params — specifically duplicate keys like `a=1&a=2` where `Rack::Utils.parse_query` and `Array(v).join(',')` reassemble differently than Shopify signs.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the reconstructed canonical string must exactly equal what Shopify signed, for any param shape
- Expected Immunefi impact: forged app-proxy request accepted (auth bypass / acting as a signed Shopify request) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
