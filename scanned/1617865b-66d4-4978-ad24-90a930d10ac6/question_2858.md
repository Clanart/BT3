# Q2858: refresh_token_if_expired!: Concurrent requests both entering `refresh_token_if_expired!` t...

## Question
Can an unprivileged attacker (timing/concurrency of requests against an expired session row) reach `refresh_token_if_expired! / perform_token_refresh! / should_refresh?` in lib/shopify_app/session/shop_session_storage.rb via an authenticated request whose offline session is expired, triggering auto-refresh, supplying concurrent requests both entering `refresh_token_if_expired!` to double-refresh and invalidate a valid refresh token, so that token refresh must be atomic and never expose or corrupt another party's token is violated, leading to token integrity loss / unauthorized session persistence? Specifically confirm that the activated session binds to the verified token's shop/user, never a disagreeing cookie/param.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `refresh_token_if_expired! / perform_token_refresh! / should_refresh?`
- Entrypoint: an authenticated request whose offline session is expired, triggering auto-refresh
- Attacker controls: timing/concurrency of requests against an expired session row — specifically concurrent requests both entering `refresh_token_if_expired!` to double-refresh and invalidate a valid refresh token.
- Exploit idea: Present a cookie and a token that disagree on shop/user and confirm the verified token wins, not the cookie.
- Invariant to test: token refresh must be atomic and never expose or corrupt another party's token
- Expected Immunefi impact: token integrity loss / unauthorized session persistence (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test with a mismatched cookie/token pair asserting the session binds to the verified token's shop/user only.
