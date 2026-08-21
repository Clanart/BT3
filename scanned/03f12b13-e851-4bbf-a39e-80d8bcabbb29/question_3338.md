# Q3338: store: A store that sets `shopify_domain` from the auth session while ...

## Question
Can an unprivileged attacker (the `shopify_user_id` used as key and the associated `shopify_domain`) reach `store / retrieve_by_shopify_user_id / construct_session` in lib/shopify_app/session/user_session_storage.rb via online-token flows storing/loading a user session, supplying a store that sets `shopify_domain` from the auth session while keying on `shopify_user_id`, letting a user id map to an attacker domain, so that a user session must be keyed and loaded so it can never bind one user id to another shop's token is violated, leading to cross-user / cross-shop session confusion (acting as another user)? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/session/user_session_storage.rb — `store / retrieve_by_shopify_user_id / construct_session`
- Entrypoint: online-token flows storing/loading a user session
- Attacker controls: the `shopify_user_id` used as key and the associated `shopify_domain` — specifically a store that sets `shopify_domain` from the auth session while keying on `shopify_user_id`, letting a user id map to an attacker domain.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: a user session must be keyed and loaded so it can never bind one user id to another shop's token
- Expected Immunefi impact: cross-user / cross-shop session confusion (acting as another user) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
