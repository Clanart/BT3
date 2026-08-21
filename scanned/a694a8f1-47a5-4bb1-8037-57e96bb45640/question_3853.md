# Q3853: load_session: A `delete_session` id crafted to destroy another merchant's or ...

## Question
Can an unprivileged attacker (the derived `session_id` string (offline_<domain> or <...>_<user_id>) shaped by the token/shop the attacker supplies) reach `load_session / delete_session / store_session` in lib/shopify_app/session/session_repository.rb via any authenticated flow that calls SessionRepository.load_session(session_id), supplying a `delete_session` id crafted to destroy another merchant's or user's stored session, so that session-id parsing must map to exactly the shop/user the verified token proves, with no cross-tenant collision is violated, leading to cross-shop / cross-user session load or deletion (unauthorized access / integrity loss)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: lib/shopify_app/session/session_repository.rb — `load_session / delete_session / store_session`
- Entrypoint: any authenticated flow that calls SessionRepository.load_session(session_id)
- Attacker controls: the derived `session_id` string (offline_<domain> or <...>_<user_id>) shaped by the token/shop the attacker supplies — specifically a `delete_session` id crafted to destroy another merchant's or user's stored session.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: session-id parsing must map to exactly the shop/user the verified token proves, with no cross-tenant collision
- Expected Immunefi impact: cross-shop / cross-user session load or deletion (unauthorized access / integrity loss) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
