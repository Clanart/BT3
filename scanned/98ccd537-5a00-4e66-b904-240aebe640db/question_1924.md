# Q1924: load_session: A session id `offline_<domain>` where `<domain>` is attacker-in...

## Question
Can an unprivileged attacker (the derived `session_id` string (offline_<domain> or <...>_<user_id>) shaped by the token/shop the attacker supplies) reach `load_session / delete_session / store_session` in lib/shopify_app/session/session_repository.rb via any authenticated flow that calls SessionRepository.load_session(session_id), supplying a session id `offline_<domain>` where `<domain>` is attacker-influenced so `retrieve_shop_session_by_shopify_domain` loads another store, so that session-id parsing must map to exactly the shop/user the verified token proves, with no cross-tenant collision is violated, leading to cross-shop / cross-user session load or deletion (unauthorized access / integrity loss)? Specifically confirm that the strongest verified identity source wins; a weaker channel cannot override it.

## Target
- File/function: lib/shopify_app/session/session_repository.rb — `load_session / delete_session / store_session`
- Entrypoint: any authenticated flow that calls SessionRepository.load_session(session_id)
- Attacker controls: the derived `session_id` string (offline_<domain> or <...>_<user_id>) shaped by the token/shop the attacker supplies — specifically a session id `offline_<domain>` where `<domain>` is attacker-influenced so `retrieve_shop_session_by_shopify_domain` loads another store.
- Exploit idea: Send the identity/token via the unexpected channel (URL param vs Authorization header vs cookie) and compare handling.
- Invariant to test: session-id parsing must map to exactly the shop/user the verified token proves, with no cross-tenant collision
- Expected Immunefi impact: cross-shop / cross-user session load or deletion (unauthorized access / integrity loss) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: test asserting header, URL-param, and cookie sources cannot be mixed to supply a weaker/attacker token.
