# Q1585: current_shopify_session: An `id_token` URL param whose JWT `dest`/`iss` shop differs fro...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying an `id_token` URL param whose JWT `dest`/`iss` shop differs from the encrypted cookie's shop, so that the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param is violated, leading to session fixation / use of another merchant's session (unauthorized data access)? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically an `id_token` URL param whose JWT `dest`/`iss` shop differs from the encrypted cookie's shop.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param
- Expected Immunefi impact: session fixation / use of another merchant's session (unauthorized data access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
