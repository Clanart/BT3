# Q3640: store: A retrieve_by_shopify_domain with an attacker domain returning ...

## Question
Can an unprivileged attacker (the `shopify_domain` used as the unique key and its casing) reach `store / retrieve_by_shopify_domain / construct_session` in lib/shopify_app/session/shop_session_storage.rb via any store/retrieve triggered by callback save_session or token exchange, supplying a retrieve_by_shopify_domain with an attacker domain returning another merchant's `shopify_token`, so that one shop row must map to exactly one canonical domain; lookup and uniqueness must agree is violated, leading to cross-shop token retrieval (access-token theft) via key normalization mismatch? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `store / retrieve_by_shopify_domain / construct_session`
- Entrypoint: any store/retrieve triggered by callback save_session or token exchange
- Attacker controls: the `shopify_domain` used as the unique key and its casing — specifically a retrieve_by_shopify_domain with an attacker domain returning another merchant's `shopify_token`.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: one shop row must map to exactly one canonical domain; lookup and uniqueness must agree
- Expected Immunefi impact: cross-shop token retrieval (access-token theft) via key normalization mismatch (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
