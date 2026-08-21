# Q3546: new: A `shop` with a dot so `sanitized_shop_name.split('.').first` y...

## Question
Can an unprivileged attacker (`shop`, `return_to`, `host`, `top_level`, `embedded`, and the Referer header) reach `new / create / authenticate / start_install / start_oauth` in app/controllers/shopify_app/sessions_controller.rb via GET/POST /login (public, unauthenticated), supplying a `shop` with a dot so `sanitized_shop_name.split('.').first` yields an attacker-chosen store handle in `unified_admin_path`, so that the install/OAuth target store must be the sanitized, attacker-independent shop, and return_to same-origin is violated, leading to OAuth flow initiated against an attacker-chosen store / open redirect on login? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: app/controllers/shopify_app/sessions_controller.rb — `new / create / authenticate / start_install / start_oauth`
- Entrypoint: GET/POST /login (public, unauthenticated)
- Attacker controls: `shop`, `return_to`, `host`, `top_level`, `embedded`, and the Referer header — specifically a `shop` with a dot so `sanitized_shop_name.split('.').first` yields an attacker-chosen store handle in `unified_admin_path`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the install/OAuth target store must be the sanitized, attacker-independent shop, and return_to same-origin
- Expected Immunefi impact: OAuth flow initiated against an attacker-chosen store / open redirect on login (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
