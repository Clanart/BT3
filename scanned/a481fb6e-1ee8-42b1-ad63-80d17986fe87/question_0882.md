# Q0882: valid_session_token?: A token whose signature is invalid but body parses, still satis...

## Question
Can an unprivileged attacker (the presence/format of an `id_token` (URL param or Authorization header) and the request origin) reach `valid_session_token?` in lib/shopify_app/controller_concerns/csrf_protection.rb via a cross-origin state-changing POST to any EnsureHasSession controller action, supplying a token whose signature is invalid but body parses, still satisfying `jwt_payload.present?` and skipping CSRF, so that CSRF may only be skipped when a fully verified session token is present, not a merely parseable one is violated, leading to CSRF on a state-changing app action (unauthorized state modification, not logout/login)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: lib/shopify_app/controller_concerns/csrf_protection.rb — `valid_session_token?`
- Entrypoint: a cross-origin state-changing POST to any EnsureHasSession controller action
- Attacker controls: the presence/format of an `id_token` (URL param or Authorization header) and the request origin — specifically a token whose signature is invalid but body parses, still satisfying `jwt_payload.present?` and skipping CSRF.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: CSRF may only be skipped when a fully verified session token is present, not a merely parseable one
- Expected Immunefi impact: CSRF on a state-changing app action (unauthorized state modification, not logout/login) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
