# Q2438: destroy: A `return_address` after logout reflecting attacker `shop`/`hos...

## Question
Can an unprivileged attacker (`shop`, `host`, the OAuth state cookie) reach `destroy / redirect_to_begin_oauth cookie` in app/controllers/shopify_app/sessions_controller.rb via GET /logout and the OAuth begin cookie set, supplying a `return_address` after logout reflecting attacker `shop`/`host` params, so that OAuth state/nonce must be unpredictable and bound to this browser only is violated, leading to OAuth CSRF / authorization-code interception (account takeover, not plain logout CSRF)? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: app/controllers/shopify_app/sessions_controller.rb — `destroy / redirect_to_begin_oauth cookie`
- Entrypoint: GET /logout and the OAuth begin cookie set
- Attacker controls: `shop`, `host`, the OAuth state cookie — specifically a `return_address` after logout reflecting attacker `shop`/`host` params.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: OAuth state/nonce must be unpredictable and bound to this browser only
- Expected Immunefi impact: OAuth CSRF / authorization-code interception (account takeover, not plain logout CSRF) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
