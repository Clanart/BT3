# Q3512: current_shopify_session_id: An `id_token` whose signature is invalid but still parses, prob...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying an `id_token` whose signature is invalid but still parses, probing whether session_id is derived before verification, so that session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange is violated, leading to authentication bypass / minting an access token for a shop the attacker does not own? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically an `id_token` whose signature is invalid but still parses, probing whether session_id is derived before verification.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange
- Expected Immunefi impact: authentication bypass / minting an access token for a shop the attacker does not own (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
