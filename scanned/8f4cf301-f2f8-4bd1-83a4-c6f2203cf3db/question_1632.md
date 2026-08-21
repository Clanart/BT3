# Q1632: sanitized_params: `embedded=1` or a spoofed `Sec-Fetch-Dest: iframe` header to fo...

## Question
Can an unprivileged attacker (`shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body) reach `sanitized_params / referer_sanitized_shop_name / embedded?` in lib/shopify_app/controller_concerns/sanitized_params.rb via any controller reading params through the SanitizedParams concern, supplying `embedded=1` or a spoofed `Sec-Fetch-Dest: iframe` header to force `embedded?` true and change redirect/framing behavior, so that a non-string or referer-sourced shop must still be sanitized before any trust decision is violated, leading to cross-shop access / open redirect from an unsanitized shop value? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/controller_concerns/sanitized_params.rb — `sanitized_params / referer_sanitized_shop_name / embedded?`
- Entrypoint: any controller reading params through the SanitizedParams concern
- Attacker controls: `shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body — specifically `embedded=1` or a spoofed `Sec-Fetch-Dest: iframe` header to force `embedded?` true and change redirect/framing behavior.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: a non-string or referer-sourced shop must still be sanitized before any trust decision
- Expected Immunefi impact: cross-shop access / open redirect from an unsanitized shop value (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
