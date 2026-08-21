# Q2584: sanitized_params: A non-String `shop` (array/hash) so `params[:shop].is_a?(String...

## Question
Can an unprivileged attacker (`shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body) reach `sanitized_params / referer_sanitized_shop_name / embedded?` in lib/shopify_app/controller_concerns/sanitized_params.rb via any controller reading params through the SanitizedParams concern, supplying a non-String `shop` (array/hash) so `params[:shop].is_a?(String)` is false and sanitization is skipped, leaving raw shop downstream, so that a non-string or referer-sourced shop must still be sanitized before any trust decision is violated, leading to cross-shop access / open redirect from an unsanitized shop value? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/sanitized_params.rb — `sanitized_params / referer_sanitized_shop_name / embedded?`
- Entrypoint: any controller reading params through the SanitizedParams concern
- Attacker controls: `shop`, the Referer header, `embedded`, Sec-Fetch-Dest header, and POST vs GET body — specifically a non-String `shop` (array/hash) so `params[:shop].is_a?(String)` is false and sanitization is skipped, leaving raw shop downstream.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: a non-string or referer-sourced shop must still be sanitized before any trust decision
- Expected Immunefi impact: cross-shop access / open redirect from an unsanitized shop value (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
