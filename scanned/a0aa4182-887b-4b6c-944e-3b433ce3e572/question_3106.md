# Q3106: valid_session_token?: A cross-site POST that carries any parseable `id_token` param s...

## Question
Can an unprivileged attacker (the presence/format of an `id_token` (URL param or Authorization header) and the request origin) reach `valid_session_token?` in lib/shopify_app/controller_concerns/csrf_protection.rb via a cross-origin state-changing POST to any EnsureHasSession controller action, supplying a cross-site POST that carries any parseable `id_token` param so `jwt_payload.present?` disables `protect_from_forgery`, so that CSRF may only be skipped when a fully verified session token is present, not a merely parseable one is violated, leading to CSRF on a state-changing app action (unauthorized state modification, not logout/login)? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/csrf_protection.rb — `valid_session_token?`
- Entrypoint: a cross-origin state-changing POST to any EnsureHasSession controller action
- Attacker controls: the presence/format of an `id_token` (URL param or Authorization header) and the request origin — specifically a cross-site POST that carries any parseable `id_token` param so `jwt_payload.present?` disables `protect_from_forgery`.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: CSRF may only be skipped when a fully verified session token is present, not a merely parseable one
- Expected Immunefi impact: CSRF on a state-changing app action (unauthorized state modification, not logout/login) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
