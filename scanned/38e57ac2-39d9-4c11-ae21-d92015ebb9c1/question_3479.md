# Q3479: current_shopify_session: An `id_token` URL param whose JWT `dest`/`iss` shop differs fro...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying an `id_token` URL param whose JWT `dest`/`iss` shop differs from the encrypted cookie's shop, so that the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param is violated, leading to session fixation / use of another merchant's session (unauthorized data access)? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically an `id_token` URL param whose JWT `dest`/`iss` shop differs from the encrypted cookie's shop.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param
- Expected Immunefi impact: session fixation / use of another merchant's session (unauthorized data access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
