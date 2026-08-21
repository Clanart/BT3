# Q3345: current_shopify_session: A `shop` param that differs from `current_shopify_session.shop`...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying a `shop` param that differs from `current_shopify_session.shop` to probe `session_shop_conflicts_with_params` bypass, so that session/shop conflict detection must force re-login whenever params disagree with the loaded session is violated, leading to cross-user session confusion (acting as another user)? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically a `shop` param that differs from `current_shopify_session.shop` to probe `session_shop_conflicts_with_params` bypass.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: session/shop conflict detection must force re-login whenever params disagree with the loaded session
- Expected Immunefi impact: cross-user session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
