# Q3739: current_shopify_session_id: A blank `requested_shopify_domain` so the mismatch guard return...

## Question
Can an unprivileged attacker (the `id_token` (Authorization header or URL param) and the `shop`/`host` query params) reach `current_shopify_session_id / retrieve_session_from_token_exchange` in lib/shopify_app/controller_concerns/token_exchange.rb via GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy, supplying a blank `requested_shopify_domain` so the mismatch guard returns false and skips shop validation, so that token exchange must not persist a session for a shop asserted only by an unverified claim is violated, leading to unauthorized offline access-token acquisition (token theft)? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/controller_concerns/token_exchange.rb — `current_shopify_session_id / retrieve_session_from_token_exchange`
- Entrypoint: GET/POST any EnsureHasSession or EnsureInstalled action under the new embedded auth strategy
- Attacker controls: the `id_token` (Authorization header or URL param) and the `shop`/`host` query params — specifically a blank `requested_shopify_domain` so the mismatch guard returns false and skips shop validation.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: token exchange must not persist a session for a shop asserted only by an unverified claim
- Expected Immunefi impact: unauthorized offline access-token acquisition (token theft) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
