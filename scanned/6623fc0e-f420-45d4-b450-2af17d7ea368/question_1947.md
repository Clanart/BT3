# Q1947: redirect_to_app: A base64 `host` that `decoded_host` -> `embedded_app_url` build...

## Question
Can an unprivileged attacker (`host` (base64) and `return_to` (session, originally attacker-seeded via login)) reach `redirect_to_app / decoded_host / deduced_phishing_attack? / fully_formed_url?` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback completion redirect, supplying a base64 `host` that `decoded_host` -> `embedded_app_url` builds, with `sanitize_shop_domain(URI(decoded_host).host)` as the only guard, so that the post-auth redirect must land on the app inside the acting shop's admin, never an attacker origin is violated, leading to open redirect delivering the freshly minted session/host to an attacker (token exfiltration)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `redirect_to_app / decoded_host / deduced_phishing_attack? / fully_formed_url?`
- Entrypoint: GET /auth/shopify/callback completion redirect
- Attacker controls: `host` (base64) and `return_to` (session, originally attacker-seeded via login) — specifically a base64 `host` that `decoded_host` -> `embedded_app_url` builds, with `sanitize_shop_domain(URI(decoded_host).host)` as the only guard.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: the post-auth redirect must land on the app inside the acting shop's admin, never an attacker origin
- Expected Immunefi impact: open redirect delivering the freshly minted session/host to an attacker (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
