# Q0214: valid_session_token?: An `id_token` URL param on a form POST to disable CSRF without ...

## Question
Can an unprivileged attacker (the presence/format of an `id_token` (URL param or Authorization header) and the request origin) reach `valid_session_token?` in lib/shopify_app/controller_concerns/csrf_protection.rb via a cross-origin state-changing POST to any EnsureHasSession controller action, supplying an `id_token` URL param on a form POST to disable CSRF without a valid Bearer header, so that CSRF may only be skipped when a fully verified session token is present, not a merely parseable one is violated, leading to CSRF on a state-changing app action (unauthorized state modification, not logout/login)? Specifically confirm that the crafted request is rejected or scoped to the attacker's own shop.

## Target
- File/function: lib/shopify_app/controller_concerns/csrf_protection.rb — `valid_session_token?`
- Entrypoint: a cross-origin state-changing POST to any EnsureHasSession controller action
- Attacker controls: the presence/format of an `id_token` (URL param or Authorization header) and the request origin — specifically an `id_token` URL param on a form POST to disable CSRF without a valid Bearer header.
- Exploit idea: Craft the single HTTP request above against a default app install and confirm the boundary holds.
- Invariant to test: CSRF may only be skipped when a fully verified session token is present, not a merely parseable one
- Expected Immunefi impact: CSRF on a state-changing app action (unauthorized state modification, not logout/login) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: controller/request spec issuing the crafted request and asserting a 401/redirect-to-own-origin, not access.
