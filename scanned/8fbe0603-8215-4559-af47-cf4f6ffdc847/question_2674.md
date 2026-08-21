# Q2674: perform: An after_authenticate job dispatched with `shop_domain: session...

## Question
Can an unprivileged attacker (the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job) reach `perform / shop_session / install_webhooks` in lib/shopify_app/auth/post_authenticate_tasks.rb via completion of OAuth callback or token exchange (attacker-initiated install of their own app context), supplying an after_authenticate job dispatched with `shop_domain: session.shop` where shop is attacker-influenced, so that post-auth side effects must run with the exact verified shop's own token is violated, leading to cross-shop side effects / token misuse during install? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/auth/post_authenticate_tasks.rb — `perform / shop_session / install_webhooks`
- Entrypoint: completion of OAuth callback or token exchange (attacker-initiated install of their own app context)
- Attacker controls: the `session.shop` that selects which shop session installs webhooks/scripttags and runs the after_authenticate job — specifically an after_authenticate job dispatched with `shop_domain: session.shop` where shop is attacker-influenced.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: post-auth side effects must run with the exact verified shop's own token
- Expected Immunefi impact: cross-shop side effects / token misuse during install (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
