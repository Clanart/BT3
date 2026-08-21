# Q2727: current_shopify_session: An online-vs-offline confusion where `online_token_configured?`...

## Question
Can an unprivileged attacker (the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value) reach `current_shopify_session / load_current_session` in lib/shopify_app/controller_concerns/login_protection.rb via GET any EnsureHasSession-protected controller action (legacy auth strategy), supplying an online-vs-offline confusion where `online_token_configured?` disagrees with the token type in the id_token, so that the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param is violated, leading to session fixation / use of another merchant's session (unauthorized data access)? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `current_shopify_session / load_current_session`
- Entrypoint: GET any EnsureHasSession-protected controller action (legacy auth strategy)
- Attacker controls: the `id_token` URL param, the Authorization: Bearer header, `shop`, `host`, `session`, and the encrypted session cookie value — specifically an online-vs-offline confusion where `online_token_configured?` disagrees with the token type in the id_token.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: the activated session must belong to the shop/user proven by a verified id_token, not by an unverified cookie or param
- Expected Immunefi impact: session fixation / use of another merchant's session (unauthorized data access) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
