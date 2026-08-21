# Q3285: current_shopify_session_id: A blank `requested_shopify_domain` so the mismatch guard return...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying a blank `requested_shopify_domain` so the mismatch guard returns false and skips shop validation, so that requested vs authenticated shop mismatch must block the request, not fall through on a blank value is violated, leading to cross-shop access using a valid token for a different store? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically a blank `requested_shopify_domain` so the mismatch guard returns false and skips shop validation.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: requested vs authenticated shop mismatch must block the request, not fall through on a blank value
- Expected Immunefi impact: cross-shop access using a valid token for a different store (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
