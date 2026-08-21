# Q3303: sanitized_params: A POST where `request.request_parameters` vs `query_parameters`...

## Question
Can an unprivileged attacker (`shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body) reach `sanitized_params / referer_sanitized_shop_name / embedded?` in lib/shopify_app/controller_concerns/sanitized_params.rb via any controller reading params through the SanitizedParams concern, supplying a POST where `request.request_parameters` vs `query_parameters` diverge, so the sanitized copy misses the acted-on value, so that a non-string or referer-sourced shop must still be sanitized before any trust decision is violated, leading to cross-shop access / open redirect from an unsanitized shop value? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/sanitized_params.rb — `sanitized_params / referer_sanitized_shop_name / embedded?`
- Entrypoint: any controller reading params through the SanitizedParams concern
- Attacker controls: `shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body — specifically a POST where `request.request_parameters` vs `query_parameters` diverge, so the sanitized copy misses the acted-on value.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: a non-string or referer-sourced shop must still be sanitized before any trust decision
- Expected Immunefi impact: cross-shop access / open redirect from an unsanitized shop value (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
