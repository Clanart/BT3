# Q3073: redirect_to_app: A `return_to` relative path joined to attacker `decoded_host` p...

## Question
Can an unprivileged attacker (`host` (base64) and `return_to` (session, originally attacker-seeded via login)) reach `redirect_to_app / decoded_host / deduced_phishing_attack? / fully_formed_url?` in app/controllers/shopify_app/callback_controller.rb via GET /auth/shopify/callback completion redirect, supplying a `return_to` relative path joined to attacker `decoded_host` producing an external absolute URL, so that the post-auth redirect must land on the app inside the acting shop's admin, never an attacker origin is violated, leading to open redirect delivering the freshly minted session/host to an attacker (token exfiltration)? Specifically confirm that a nil/blank derived value fails closed and never becomes a trusted default.

## Target
- File/function: app/controllers/shopify_app/callback_controller.rb — `redirect_to_app / decoded_host / deduced_phishing_attack? / fully_formed_url?`
- Entrypoint: GET /auth/shopify/callback completion redirect
- Attacker controls: `host` (base64) and `return_to` (session, originally attacker-seeded via login) — specifically a `return_to` relative path joined to attacker `decoded_host` producing an external absolute URL.
- Exploit idea: Drive the input to nil/blank/empty and confirm the code fails closed rather than defaulting to a trusted value.
- Invariant to test: the post-auth redirect must land on the app inside the acting shop's admin, never an attacker origin
- Expected Immunefi impact: open redirect delivering the freshly minted session/host to an attacker (token exfiltration) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: boundary test feeding nil/blank/empty and asserting a hard reject, never a wildcard/default shop or skipped check.
