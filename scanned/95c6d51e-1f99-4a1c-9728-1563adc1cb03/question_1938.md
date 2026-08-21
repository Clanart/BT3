# Q1938: with_token_refetch: A token exchange on retry that mints a session for a shop diffe...

## Question
Can an unprivileged attacker (the `shopify_id_token` reused for the refetch) reach `with_token_refetch` in lib/shopify_app/admin_api/with_token_refetch.rb via an authenticated API call that receives a 401 and retries via token exchange, supplying a token exchange on retry that mints a session for a shop different from the original `session`, so that the refetched token must belong to the same verified shop/user as the failing session is violated, leading to cross-shop token substitution during retry (token confusion)? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/admin_api/with_token_refetch.rb — `with_token_refetch`
- Entrypoint: an authenticated API call that receives a 401 and retries via token exchange
- Attacker controls: the `shopify_id_token` reused for the refetch — specifically a token exchange on retry that mints a session for a shop different from the original `session`.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: the refetched token must belong to the same verified shop/user as the failing session
- Expected Immunefi impact: cross-shop token substitution during retry (token confusion) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
