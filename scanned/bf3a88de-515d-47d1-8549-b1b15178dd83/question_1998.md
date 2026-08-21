# Q1998: perform: An online session whose `shop_session(session)` retrieval retur...

## Question
Can an unprivileged attacker (the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job) reach `perform / shop_session / install_webhooks` in lib/shopify_app/auth/post_authenticate_tasks.rb via completion of OAuth callback or token exchange (attacker-initiated install of their own app context), supplying an online session whose `shop_session(session)` retrieval returns a different shop's offline session for webhook install, so that post-auth side effects must run with the exact verified shop's own token is violated, leading to cross-shop side effects / token misuse during install? Specifically confirm that a wrong-secret or tampered artifact is always rejected before any side effect.

## Target
- File/function: lib/shopify_app/auth/post_authenticate_tasks.rb — `perform / shop_session / install_webhooks`
- Entrypoint: completion of OAuth callback or token exchange (attacker-initiated install of their own app context)
- Attacker controls: the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job — specifically an online session whose `shop_session(session)` retrieval returns a different shop's offline session for webhook install.
- Exploit idea: Run the exact flow with a deliberately-wrong secret/signature/token to prove verification actually rejects it.
- Invariant to test: post-auth side effects must run with the exact verified shop's own token
- Expected Immunefi impact: cross-shop side effects / token misuse during install (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: negative-control test asserting a wrong-secret/wrong-signature/tampered-token request is rejected with no side effect.
