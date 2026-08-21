# Q2697: refresh_token_if_expired!: A row-lock/reload sequence where `should_refresh?` is re-evalua...

## Question
Can an unprivileged attacker (timing/concurrency of requests against an expired session row) reach `refresh_token_if_expired! / perform_token_refresh! / should_refresh?` in lib/shopify_app/session/shop_session_storage.rb via an authenticated request whose offline session is expired, triggering auto-refresh, supplying a row-lock/reload sequence where `should_refresh?` is re-evaluated but a stale token is used, so that token refresh must be atomic and never expose or corrupt another party's token is violated, leading to token integrity loss / unauthorized session persistence? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `refresh_token_if_expired! / perform_token_refresh! / should_refresh?`
- Entrypoint: an authenticated request whose offline session is expired, triggering auto-refresh
- Attacker controls: timing/concurrency of requests against an expired session row — specifically a row-lock/reload sequence where `should_refresh?` is re-evaluated but a stale token is used.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: token refresh must be atomic and never expose or corrupt another party's token
- Expected Immunefi impact: token integrity loss / unauthorized session persistence (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
