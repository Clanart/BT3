# Q2876: store: A retrieve_by_shopify_domain with an attacker domain returning ...

## Question
Can an unprivileged attacker (the `shopify_domain` used as the unique key and its casing) reach `store / retrieve_by_shopify_domain / construct_session` in lib/shopify_app/session/shop_session_storage.rb via any store/retrieve triggered by callback save_session or token exchange, supplying a retrieve_by_shopify_domain with an attacker domain returning another merchant's `shopify_token`, so that one shop row must map to exactly one canonical domain; lookup and uniqueness must agree is violated, leading to cross-shop token retrieval (access-token theft) via key normalization mismatch? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `store / retrieve_by_shopify_domain / construct_session`
- Entrypoint: any store/retrieve triggered by callback save_session or token exchange
- Attacker controls: the `shopify_domain` used as the unique key and its casing — specifically a retrieve_by_shopify_domain with an attacker domain returning another merchant's `shopify_token`.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: one shop row must map to exactly one canonical domain; lookup and uniqueness must agree
- Expected Immunefi impact: cross-shop token retrieval (access-token theft) via key normalization mismatch (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
