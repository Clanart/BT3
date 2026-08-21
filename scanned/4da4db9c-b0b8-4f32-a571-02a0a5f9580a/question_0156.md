# Q0156: load_session: A domain containing `_` that confuses the `offline_(.*)` regex ...

## Question
Can an unprivileged attacker (the derived `session_id` string (offline_<domain> or <...>_<user_id>) shaped by the token/shop the attacker supplies) reach `load_session / delete_session / store_session` in lib/shopify_app/session/session_repository.rb via any authenticated flow that calls SessionRepository.load_session(session_id), supplying a domain containing `_` that confuses the `offline_(.*)` regex vs the `split('_')` user branch, so that session-id parsing must map to exactly the shop/user the verified token proves, with no cross-tenant collision is violated, leading to cross-shop / cross-user session load or deletion (unauthorized access / integrity loss)? Specifically confirm that the derived shop/user/signature equals the canonical Shopify-asserted value for every input.

## Target
- File/function: lib/shopify_app/session/session_repository.rb — `load_session / delete_session / store_session`
- Entrypoint: any authenticated flow that calls SessionRepository.load_session(session_id)
- Attacker controls: the derived `session_id` string (offline_<domain> or <...>_<user_id>) shaped by the token/shop the attacker supplies — specifically a domain containing `_` that confuses the `offline_(.*)` regex vs the `split('_')` user branch.
- Exploit idea: Compare the value the gem derives from the attacker input against the value Shopify actually signed/asserted.
- Invariant to test: session-id parsing must map to exactly the shop/user the verified token proves, with no cross-tenant collision
- Expected Immunefi impact: cross-shop / cross-user session load or deletion (unauthorized access / integrity loss) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: differential test: feed the crafted input to the parsing/verification method and diff derived vs. canonical shop/user/signature.
