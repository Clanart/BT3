# Q2002: current_shopify_session_id: An id_token replayed after the exchange to obtain a persisted o...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying an id_token replayed after the exchange to obtain a persisted offline token for a shop the attacker only transiently controls, so that requested vs authenticated shop mismatch must block the request, not fall through on a blank value is violated, leading to cross-shop access using a valid token for a different store? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically an id_token replayed after the exchange to obtain a persisted offline token for a shop the attacker only transiently controls.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: requested vs authenticated shop mismatch must block the request, not fall through on a blank value
- Expected Immunefi impact: cross-shop access using a valid token for a different store (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
