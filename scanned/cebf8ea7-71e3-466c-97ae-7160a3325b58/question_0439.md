# Q0439: refresh_token_if_expired!: A `RefreshTokenExpiredError` path that leaves the session usabl...

## Question
Can an unprivileged attacker (timing/concurrency of requests against an expired session row) reach `refresh_token_if_expired! / perform_token_refresh! / should_refresh?` in lib/shopify_app/session/shop_session_storage.rb via an authenticated request whose offline session is expired, triggering auto-refresh, supplying a `RefreshTokenExpiredError` path that leaves the session usable or leaks token state, so that token refresh must be atomic and never expose or corrupt another party's token is violated, leading to token integrity loss / unauthorized session persistence? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `refresh_token_if_expired! / perform_token_refresh! / should_refresh?`
- Entrypoint: an authenticated request whose offline session is expired, triggering auto-refresh
- Attacker controls: timing/concurrency of requests against an expired session row — specifically a `RefreshTokenExpiredError` path that leaves the session usable or leaks token state.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: token refresh must be atomic and never expose or corrupt another party's token
- Expected Immunefi impact: token integrity loss / unauthorized session persistence (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
