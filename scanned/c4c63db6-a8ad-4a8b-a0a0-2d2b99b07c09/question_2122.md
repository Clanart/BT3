# Q2122: query_string_valid?: Array-style params `a[]=1&a[]=2` altering the `sorted_params` c...

## Question
Can an unprivileged attacker (the entire query string including `signature` and repeated/array params) reach `query_string_valid? / calculated_signature` in lib/shopify_app/controller_concerns/app_proxy_verification.rb via GET/POST an app-proxy-protected controller action, supplying array-style params `a[]=1&a[]=2` altering the `sorted_params` concatenation vs the signed canonical form, so that the reconstructed canonical string must exactly equal what Shopify signed, for any param shape is violated, leading to forged app-proxy request accepted (auth bypass / acting as a signed Shopify request)? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/controller_concerns/app_proxy_verification.rb — `query_string_valid? / calculated_signature`
- Entrypoint: GET/POST an app-proxy-protected controller action
- Attacker controls: the entire query string including `signature` and repeated/array params — specifically array-style params `a[]=1&a[]=2` altering the `sorted_params` concatenation vs the signed canonical form.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: the reconstructed canonical string must exactly equal what Shopify signed, for any param shape
- Expected Immunefi impact: forged app-proxy request accepted (auth bypass / acting as a signed Shopify request) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
