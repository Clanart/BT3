# Q2761: current_shopify_session_id: An `id_token` whose signature is invalid but still parses, prob...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying an `id_token` whose signature is invalid but still parses, probing whether session_id is derived before verification, so that session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange is violated, leading to authentication bypass / minting an access token for a shop the attacker does not own? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically an `id_token` whose signature is invalid but still parses, probing whether session_id is derived before verification.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange
- Expected Immunefi impact: authentication bypass / minting an access token for a shop the attacker does not own (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
