# Q3375: current_shopify_session_id: Online-vs-offline confusion where `online_token_configured?` pi...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying online-vs-offline confusion where `online_token_configured?` picks the wrong session_id namespace, so that requested vs authenticated shop mismatch must block the request, not fall through on a blank value is violated, leading to cross-shop access using a valid token for a different store? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically online-vs-offline confusion where `online_token_configured?` picks the wrong session_id namespace.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: requested vs authenticated shop mismatch must block the request, not fall through on a blank value
- Expected Immunefi impact: cross-shop access using a valid token for a different store (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
