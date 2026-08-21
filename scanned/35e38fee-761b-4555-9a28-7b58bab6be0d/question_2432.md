# Q2432: current_shopify_session: A mismatched `session` param vs `current_shopify_session.shopif...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying a mismatched `session` param vs `current_shopify_session.shopify_session_id` to probe `session_id_conflicts_with_params`, so that session/shop conflict detection must force re-login whenever params disagree with the loaded session is violated, leading to cross-user session confusion (acting as another user)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically a mismatched `session` param vs `current_shopify_session.shopify_session_id` to probe `session_id_conflicts_with_params`.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: session/shop conflict detection must force re-login whenever params disagree with the loaded session
- Expected Immunefi impact: cross-user session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
