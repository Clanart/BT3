# Q0659: perform: An after_authenticate job dispatched with `shop_domain: session...

## Question
Can an unprivileged attacker (the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job) reach `perform / shop_session / install_webhooks` in lib/shopify_app/auth/post_authenticate_tasks.rb via completion of OAuth callback or token exchange (attacker-initiated install of their own app context), supplying an after_authenticate job dispatched with `shop_domain: session.shop` where shop is attacker-influenced, so that post-auth side effects must run with the exact verified shop's own token is violated, leading to cross-shop side effects / token misuse during install? Specifically confirm that concurrent execution produces exactly one consistent session/token with no cross-tenant leakage.

## Target
- File/function: lib/shopify_app/auth/post_authenticate_tasks.rb — `perform / shop_session / install_webhooks`
- Entrypoint: completion of OAuth callback or token exchange (attacker-initiated install of their own app context)
- Attacker controls: the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job — specifically an after_authenticate job dispatched with `shop_domain: session.shop` where shop is attacker-influenced.
- Exploit idea: Fire the flow twice in parallel to expose non-atomic checks or double-writes.
- Invariant to test: post-auth side effects must run with the exact verified shop's own token
- Expected Immunefi impact: cross-shop side effects / token misuse during install (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: concurrency test running the flow on N threads and asserting exactly one row/token and no cross-tenant write.
