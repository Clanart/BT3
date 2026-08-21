# Q3676: valid_session_token?: A token borrowed from the victim's own embedded context replaye...

## Question
Can an unprivileged attacker (the presence/format of an `id_token` (URL param or Authorization header) and the request origin) reach `valid_session_token?` in lib/shopify_app/controller_concerns/csrf_protection.rb via a cross-origin state-changing POST to any EnsureHasSession controller action, supplying a token borrowed from the victim's own embedded context replayed cross-site to skip the forgery check, so that CSRF may only be skipped when a fully verified session token is present, not a merely parseable one is violated, leading to CSRF on a state-changing app action (unauthorized state modification, not logout/login)? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/controller_concerns/csrf_protection.rb — `valid_session_token?`
- Entrypoint: a cross-origin state-changing POST to any EnsureHasSession controller action
- Attacker controls: the presence/format of an `id_token` (URL param or Authorization header) and the request origin — specifically a token borrowed from the victim's own embedded context replayed cross-site to skip the forgery check.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: CSRF may only be skipped when a fully verified session token is present, not a merely parseable one
- Expected Immunefi impact: CSRF on a state-changing app action (unauthorized state modification, not logout/login) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
