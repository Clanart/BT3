# Q3509: current_shopify_session_id: An expired id_token with `check_session_expiry_date` true to fo...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying an expired id_token with `check_session_expiry_date` true to force `should_exchange_expired_token?` and a fresh exchange, so that session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange is violated, leading to authentication bypass / minting an access token for a shop the attacker does not own? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically an expired id_token with `check_session_expiry_date` true to force `should_exchange_expired_token?` and a fresh exchange.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange
- Expected Immunefi impact: authentication bypass / minting an access token for a shop the attacker does not own (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
