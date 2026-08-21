# Q3708: current_shopify_session: A request with NO id_token and only a cookie, forcing `current_...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying a request with NO id_token and only a cookie, forcing `current_session_id` to derive identity from the cookie alone, so that the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param is violated, leading to session fixation / use of another merchant's session (unauthorized data access)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically a request with NO id_token and only a cookie, forcing `current_session_id` to derive identity from the cookie alone.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param
- Expected Immunefi impact: session fixation / use of another merchant's session (unauthorized data access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
