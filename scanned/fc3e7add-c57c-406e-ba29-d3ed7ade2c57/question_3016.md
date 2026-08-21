# Q3016: query_string_valid?: Array-style params `a[]=1&a[]=2` altering the `sorted_params` c...

## Question
Can an unprivileged attacker (the entire query string including `signature` and repeated/array params) reach `query_string_valid? / calculated_signature` in lib/shopify_app/controller_concerns/app_proxy_verification.rb via GET/POST an app-proxy-protected controller action, supplying array-style params `a[]=1&a[]=2` altering the `sorted_params` concatenation vs the signed canonical form, so that the reconstructed canonical string must exactly equal what Shopify signed, for any param shape is violated, leading to forged app-proxy request accepted (auth bypass / acting as a signed Shopify request)? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/app_proxy_verification.rb — `query_string_valid? / calculated_signature`
- Entrypoint: GET/POST an app-proxy-protected controller action
- Attacker controls: the entire query string including `signature` and repeated/array params — specifically array-style params `a[]=1&a[]=2` altering the `sorted_params` concatenation vs the signed canonical form.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: the reconstructed canonical string must exactly equal what Shopify signed, for any param shape
- Expected Immunefi impact: forged app-proxy request accepted (auth bypass / acting as a signed Shopify request) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
