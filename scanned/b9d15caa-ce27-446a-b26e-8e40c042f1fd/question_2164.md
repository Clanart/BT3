# Q2164: store: A retrieve_by_shopify_domain with an attacker domain returning ...

## Question
Can an unprivileged attacker (the `shopify_domain` used as the unique key and its casing) reach `store / retrieve_by_shopify_domain / construct_session` in lib/shopify_app/session/shop_session_storage.rb via any store/retrieve triggered by callback save_session or token exchange, supplying a retrieve_by_shopify_domain with an attacker domain returning another merchant's `shopify_token`, so that one shop row must map to exactly one canonical domain; lookup and uniqueness must agree is violated, leading to cross-shop token retrieval (access-token theft) via key normalization mismatch? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/session/shop_session_storage.rb — `store / retrieve_by_shopify_domain / construct_session`
- Entrypoint: any store/retrieve triggered by callback save_session or token exchange
- Attacker controls: the `shopify_domain` used as the unique key and its casing — specifically a retrieve_by_shopify_domain with an attacker domain returning another merchant's `shopify_token`.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: one shop row must map to exactly one canonical domain; lookup and uniqueness must agree
- Expected Immunefi impact: cross-shop token retrieval (access-token theft) via key normalization mismatch (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
