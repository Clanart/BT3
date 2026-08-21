# Q0191: start_user_token_flow?: A user id in the cookie that selects another user's scope durin...

## Question
Can an unprivileged attacker (the `session[:shopify_user_id]` cookie and the online/offline session type) reach `start_user_token_flow? / update_user_access_scopes? / update_rails_cookie` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback with user storage configured, supplying a user id in the cookie that selects another user's scope during `user_access_scopes_strategy.update_access_scopes?`, so that the shopify_user_id used for scope decisions must come from the verified session, not a mutable Rails cookie is violated, leading to cross-user scope/identity confusion? Specifically confirm that a replayed valid artifact yields no new session, token, or side effect.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `start_user_token_flow? / update_user_access_scopes? / update_rails_cookie`
- Entrypoint: GET /auth/shopify/callback with user storage configured
- Attacker controls: the `session[:shopify_user_id]` cookie and the online/offline session type — specifically a user id in the cookie that selects another user's scope during `user_access_scopes_strategy.update_access_scopes?`.
- Exploit idea: Replay a previously-valid artifact (token, HMAC body, OAuth code, callback) and confirm it is not re-accepted.
- Invariant to test: the shopify_user_id used for scope decisions must come from the verified session, not a mutable Rails cookie
- Expected Immunefi impact: cross-user scope/identity confusion (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: replay test capturing one valid flow and re-submitting it, asserting no second session/token/side-effect.
