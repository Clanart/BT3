# Q0724: current_shopify_session: A JWT whose `exp` is in the past when `check_session_expiry_dat...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying a JWT whose `exp` is in the past when `check_session_expiry_date` is false so `activate_shopify_session` skips expiry, so that the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param is violated, leading to session fixation / use of another merchant's session (unauthorized data access)? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically a JWT whose `exp` is in the past when `check_session_expiry_date` is false so `activate_shopify_session` skips expiry.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param
- Expected Immunefi impact: session fixation / use of another merchant's session (unauthorized data access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
