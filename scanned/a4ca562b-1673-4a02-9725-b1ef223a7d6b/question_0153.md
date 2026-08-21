# Q0153: current_shopify_session: An `id_token` present but malformed so `InvalidJwtTokenError` i...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying an `id_token` present but malformed so `InvalidJwtTokenError` is swallowed and `current_shopify_session` returns nil, so that session/shop conflict detection must force re-login whenever params disagree with the loaded session is violated, leading to cross-user session confusion (acting as another user)? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically an `id_token` present but malformed so `InvalidJwtTokenError` is swallowed and `current_shopify_session` returns nil.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: session/shop conflict detection must force re-login whenever params disagree with the loaded session
- Expected Immunefi impact: cross-user session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
