# Q0637: sanitized_params: A `shop` array like `shop[]=a&shop[]=b` that skips string sanit...

## Question
Can an unprivileged attacker (`shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body) reach `sanitized_params / referer_sanitized_shop_name / embedded?` in lib/shopify_app/controller_concerns/sanitized_params.rb via any controller reading params through the SanitizedParams concern, supplying a `shop` array like `shop[]=a&shop[]=b` that skips string sanitization but is later coerced, so that a non-string or referer-sourced shop must still be sanitized before any trust decision is violated, leading to cross-shop access / open redirect from an unsanitized shop value? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: lib/shopify_app/controller_concerns/sanitized_params.rb — `sanitized_params / referer_sanitized_shop_name / embedded?`
- Entrypoint: any controller reading params through the SanitizedParams concern
- Attacker controls: `shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body — specifically a `shop` array like `shop[]=a&shop[]=b` that skips string sanitization but is later coerced.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: a non-string or referer-sourced shop must still be sanitized before any trust decision
- Expected Immunefi impact: cross-shop access / open redirect from an unsanitized shop value (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
