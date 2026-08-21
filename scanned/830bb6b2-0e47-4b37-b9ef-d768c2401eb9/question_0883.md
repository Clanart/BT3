# Q0883: current_shopify_session_id: A token whose `dest` host is a look-alike accepted by `jwt_shop...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying a token whose `dest` host is a look-alike accepted by `jwt_shopify_domain` -> `sanitize_shop_domain`, so that token exchange must not persist a session for a shop asserted only by an unverified claim is violated, leading to unauthorized offline access-token acquisition (token theft)? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically a token whose `dest` host is a look-alike accepted by `jwt_shopify_domain` -> `sanitize_shop_domain`.
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: token exchange must not persist a session for a shop asserted only by an unverified claim
- Expected Immunefi impact: unauthorized offline access-token acquisition (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
