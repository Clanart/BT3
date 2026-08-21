# Q2866: new: An `api_key` reflected install URL where `client_id` is fixed b...

## Question
Can an unprivileged attacker (`shop`, `return_to`, `host`, `top_level`, `embedded`, and the Referer header) reach `new / create / authenticate / start_install / start_oauth` in app/controllers/shopify_app/sessions_controller.rb via GET/POST /login (public, unauthenticated), supplying an `api_key` reflected install URL where `client_id` is fixed but the store handle is attacker-chosen, so that the install/OAuth target store must be the sanitized, attacker-independent shop, and return_to same-origin is violated, leading to OAuth flow initiated against an attacker-chosen store / open redirect on login? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: app/controllers/shopify_app/sessions_controller.rb — `new / create / authenticate / start_install / start_oauth`
- Entrypoint: GET/POST /login (public, unauthenticated)
- Attacker controls: `shop`, `return_to`, `host`, `top_level`, `embedded`, and the Referer header — specifically an `api_key` reflected install URL where `client_id` is fixed but the store handle is attacker-chosen.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: the install/OAuth target store must be the sanitized, attacker-independent shop, and return_to same-origin
- Expected Immunefi impact: OAuth flow initiated against an attacker-chosen store / open redirect on login (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
