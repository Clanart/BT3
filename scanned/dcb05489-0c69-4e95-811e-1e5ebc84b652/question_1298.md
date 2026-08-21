# Q1298: start_user_token_flow?: A forged/absent `session[:shopify_user_id]` so `update_user_acc...

## Question
Can an unprivileged attacker (the `session[:shopify_user_id]` cookie and the online/offline session type) reach `start_user_token_flow? / update_user_access_scopes? / update_rails_cookie` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback with user storage configured, supplying a forged/absent `session[:shopify_user_id]` so `update_user_access_scopes?` returns true/false incorrectly, so that the shopify_user_id used for scope decisions must come from the verified session, not a mutable Rails cookie is violated, leading to cross-user scope/identity confusion? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `start_user_token_flow? / update_user_access_scopes? / update_rails_cookie`
- Entrypoint: GET /auth/shopify/callback with user storage configured
- Attacker controls: the `session[:shopify_user_id]` cookie and the online/offline session type — specifically a forged/absent `session[:shopify_user_id]` so `update_user_access_scopes?` returns true/false incorrectly.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: the shopify_user_id used for scope decisions must come from the verified session, not a mutable Rails cookie
- Expected Immunefi impact: cross-user scope/identity confusion (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
