# Q0519: sanitized_params: `embedded=1` or a spoofed `Sec-Fetch-Dest: iframe` header to fo...

## Question
Can an unprivileged attacker (`shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body) reach `sanitized_params / referer_sanitized_shop_name / embedded?` in lib/shopify_app/controller_concerns/sanitized_params.rb via any controller reading params through the SanitizedParams concern, supplying `embedded=1` or a spoofed `Sec-Fetch-Dest: iframe` header to force `embedded?` true and change redirect/framing behavior, so that a non-string or referer-sourced shop must still be sanitized before any trust decision is violated, leading to cross-shop access / open redirect from an unsanitized shop value? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/sanitized_params.rb — `sanitized_params / referer_sanitized_shop_name / embedded?`
- Entrypoint: any controller reading params through the SanitizedParams concern
- Attacker controls: `shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body — specifically `embedded=1` or a spoofed `Sec-Fetch-Dest: iframe` header to force `embedded?` true and change redirect/framing behavior.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: a non-string or referer-sourced shop must still be sanitized before any trust decision
- Expected Immunefi impact: cross-shop access / open redirect from an unsanitized shop value (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
