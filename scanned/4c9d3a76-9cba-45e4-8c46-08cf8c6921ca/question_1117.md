# Q1117: perform: An online session whose `shop_session(session)` retrieval retur...

## Question
Can an unprivileged attacker (the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job) reach `perform / shop_session / install_webhooks` in lib/shopify_app/auth/post_authenticate_tasks.rb via completion of OAuth callback or token exchange (attacker-initiated install of their own app context), supplying an online session whose `shop_session(session)` retrieval returns a different shop's offline session for webhook install, so that post-auth side effects must run with the exact verified shop's own token is violated, leading to cross-shop side effects / token misuse during install? Specifically confirm that the gem's canonical form matches Shopify's signed canonical form exactly.

## Target
- File/function: lib/shopify_app/auth/post_authenticate_tasks.rb — `perform / shop_session / install_webhooks`
- Entrypoint: completion of OAuth callback or token exchange (attacker-initiated install of their own app context)
- Attacker controls: the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job — specifically an online session whose `shop_session(session)` retrieval returns a different shop's offline session for webhook install.
- Exploit idea: Probe whether the gem's canonical form of shop/host/params matches Shopify's exact canonicalization.
- Invariant to test: post-auth side effects must run with the exact verified shop's own token
- Expected Immunefi impact: cross-shop side effects / token misuse during install (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: canonicalization test comparing the gem's normalized string to Shopify's signed canonical form byte-for-byte.
