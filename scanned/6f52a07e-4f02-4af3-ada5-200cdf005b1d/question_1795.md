# Q1795: current_shopify_session: A mismatched `session` param vs `current_shopify_session.shopif...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying a mismatched `session` param vs `current_shopify_session.shopify_session_id` to probe `session_id_conflicts_with_params`, so that session/shop conflict detection must force re-login whenever params disagree with the loaded session is violated, leading to cross-user session confusion (acting as another user)? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically a mismatched `session` param vs `current_shopify_session.shopify_session_id` to probe `session_id_conflicts_with_params`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: session/shop conflict detection must force re-login whenever params disagree with the loaded session
- Expected Immunefi impact: cross-user session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
