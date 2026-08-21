# Q0820: with_token_refetch: A 401 retry loop where `retrying` local is mis-scoped so infini...

## Question
Can an unprivileged attacker (the `shopify_id_token` reused for the refetch) reach `with_token_refetch` in lib/shopify_app/admin_api/with_token_refetch.rb via an authenticated API call that receives a 401 and retries via token exchange, supplying a 401 retry loop where `retrying` local is mis-scoped so infinite retries or a stale token are used, so that the refetched token must belong to the same verified shop/user as the failing session is violated, leading to cross-shop token substitution during retry (token confusion)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/admin_api/with_token_refetch.rb — `with_token_refetch`
- Entrypoint: an authenticated API call that receives a 401 and retries via token exchange
- Attacker controls: the `shopify_id_token` reused for the refetch — specifically a 401 retry loop where `retrying` local is mis-scoped so infinite retries or a stale token are used.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: the refetched token must belong to the same verified shop/user as the failing session
- Expected Immunefi impact: cross-shop token substitution during retry (token confusion) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
