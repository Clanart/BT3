# Q1587: with_token_refetch: `session.copy_attributes_from(new_session)` overwriting the ses...

## Question
Can an unprivileged attacker (the `shopify_id_token` reused for the refetch) reach `with_token_refetch` in lib/shopify_app/admin_api/with_token_refetch.rb via an authenticated API call that receives a 401 and retries via token exchange, supplying `session.copy_attributes_from(new_session)` overwriting the session with another shop's attributes, so that the refetched token must belong to the same verified shop/user as the failing session is violated, leading to cross-shop token substitution during retry (token confusion)? Specifically confirm that no encoding variant of the input is accepted as a different-but-trusted value.

## Target
- File/function: lib/shopify_app/admin_api/with_token_refetch.rb — `with_token_refetch`
- Entrypoint: an authenticated API call that receives a 401 and retries via token exchange
- Attacker controls: the `shopify_id_token` reused for the refetch — specifically `session.copy_attributes_from(new_session)` overwriting the session with another shop's attributes.
- Exploit idea: Fuzz the encoding/normalization boundary (case, unicode, punycode, base64 leniency, delimiters).
- Invariant to test: the refetched token must belong to the same verified shop/user as the failing session
- Expected Immunefi impact: cross-shop token substitution during retry (token confusion) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: property/fuzz test over encodings asserting sanitize/verify rejects every non-canonical form.
