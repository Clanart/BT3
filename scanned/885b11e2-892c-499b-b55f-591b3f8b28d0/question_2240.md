# Q2240: redirect_to_app: A callback where `ShopifyAPI::Context.embedded?` is true so the...

## Question
Can an unprivileged attacker (`host` (base64) and `return_to` (session, originally attacker-seeded via login)) reach `redirect_to_app / decoded_host / deduced_phishing_attack? / fully_formed_url?` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback completion redirect, supplying a callback where `ShopifyAPI::Context.embedded?` is true so the attacker `return_to`/host path is taken, so that the post-auth redirect must land on the app inside the acting shop's admin, never an attacker origin is violated, leading to open redirect delivering the freshly minted session/host to an attacker (token exfiltration)? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `redirect_to_app / decoded_host / deduced_phishing_attack? / fully_formed_url?`
- Entrypoint: GET /auth/shopify/callback completion redirect
- Attacker controls: `host` (base64) and `return_to` (session, originally attacker-seeded via login) — specifically a callback where `ShopifyAPI::Context.embedded?` is true so the attacker `return_to`/host path is taken.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: the post-auth redirect must land on the app inside the acting shop's admin, never an attacker origin
- Expected Immunefi impact: open redirect delivering the freshly minted session/host to an attacker (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
