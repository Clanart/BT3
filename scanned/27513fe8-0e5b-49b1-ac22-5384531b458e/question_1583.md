# Q1583: current_shopify_session_id: A token whose `dest` host is a look-alike accepted by `jwt_shop...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying a token whose `dest` host is a look-alike accepted by `jwt_shopify_domain` -> `sanitize_shop_domain`, so that session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange is violated, leading to authentication bypass / minting an access token for a shop the attacker does not own? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically a token whose `dest` host is a look-alike accepted by `jwt_shopify_domain` -> `sanitize_shop_domain`.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: session id must come from a fully verified id_token (sig, aud, dest, exp) before any load or exchange
- Expected Immunefi impact: authentication bypass / minting an access token for a shop the attacker does not own (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
