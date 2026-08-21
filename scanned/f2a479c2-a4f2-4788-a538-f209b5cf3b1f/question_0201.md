# Q0201: current_shopify_session: A `shop` param that differs from `current_shopify_session.shop`...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying a `shop` param that differs from `current_shopify_session.shop` to probe `session_shop_conflicts_with_params` bypass, so that id_token verification errors must fail closed, never yield an activated session is violated, leading to authentication bypass? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically a `shop` param that differs from `current_shopify_session.shop` to probe `session_shop_conflicts_with_params` bypass.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: id_token verification errors must fail closed, never yield an activated session
- Expected Immunefi impact: authentication bypass (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
