# Q1036: start_user_token_flow?: A user id in the cookie that selects another user's scope durin...

## Question
Can an unprivileged attacker (the `session[:shopify_user_id]` cookie and the online/offline session type) reach `start_user_token_flow? / update_user_access_scopes? / update_rails_cookie` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback with user storage configured, supplying a user id in the cookie that selects another user's scope during `user_access_scopes_strategy.update_access_scopes?`, so that the shopify_user_id used for scope decisions must come from the verified session, not a mutable Rails cookie is violated, leading to cross-user scope/identity confusion? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `start_user_token_flow? / update_user_access_scopes? / update_rails_cookie`
- Entrypoint: GET /auth/shopify/callback with user storage configured
- Attacker controls: the `session[:shopify_user_id]` cookie and the online/offline session type — specifically a user id in the cookie that selects another user's scope during `user_access_scopes_strategy.update_access_scopes?`.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: the shopify_user_id used for scope decisions must come from the verified session, not a mutable Rails cookie
- Expected Immunefi impact: cross-user scope/identity confusion (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
