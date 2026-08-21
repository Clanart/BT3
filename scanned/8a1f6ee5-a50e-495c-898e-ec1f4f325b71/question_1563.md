# Q1563: current_shopify_session_id: A token whose `dest` host is a look-alike accepted by `jwt_shop...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying a token whose `dest` host is a look-alike accepted by `jwt_shopify_domain` -> `sanitize_shop_domain`, so that token exchange must not persist a session for a shop asserted only by an unverified claim is violated, leading to unauthorized offline access-token acquisition (token theft)? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically a token whose `dest` host is a look-alike accepted by `jwt_shopify_domain` -> `sanitize_shop_domain`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: token exchange must not persist a session for a shop asserted only by an unverified claim
- Expected Immunefi impact: unauthorized offline access-token acquisition (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
