# Q2509: refresh_token_if_expired!: A `RefreshTokenExpiredError` path that leaves the session usabl...

## Question
Can an unprivileged attacker (timing/concurrency of requests against an expired session row) reach `refresh_token_if_expired! / perform_token_refresh! / should_refresh?` in lib/shopify_app/session/shop_session_storage.rb via an authenticated request whose offline session is expired, triggering auto-refresh, supplying a `RefreshTokenExpiredError` path that leaves the session usable or leaks token state, so that token refresh must be atomic and never expose or corrupt another party's token is violated, leading to token integrity loss / unauthorized session persistence? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `refresh_token_if_expired! / perform_token_refresh! / should_refresh?`
- Entrypoint: an authenticated request whose offline session is expired, triggering auto-refresh
- Attacker controls: timing/concurrency of requests against an expired session row — specifically a `RefreshTokenExpiredError` path that leaves the session usable or leaks token state.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: token refresh must be atomic and never expose or corrupt another party's token
- Expected Immunefi impact: token integrity loss / unauthorized session persistence (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
