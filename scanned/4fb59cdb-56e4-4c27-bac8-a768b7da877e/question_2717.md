# Q2717: destroy: An OAuth begin where the encrypted state cookie can be fixed/re...

## Question
Can an unprivileged attacker (`shop`, `host`, the OAuth state cookie) reach `destroy / redirect_to_begin_oauth cookie` in app/controllers/shopify_app/sessions_controller.rb via GET /logout and the OAuth begin cookie set, supplying an OAuth begin where the encrypted state cookie can be fixed/replayed to bind a victim to an attacker state, so that OAuth state/nonce must be unpredictable and bound to this browser only is violated, leading to OAuth CSRF / authorization-code interception (account takeover, not plain logout CSRF)? Specifically confirm that the malicious input stays rejected across refactors (pinned fixture).

## Target
- File/function: app/controllers/shopify_app/sessions_controller.rb — `destroy / redirect_to_begin_oauth cookie`
- Entrypoint: GET /logout and the OAuth begin cookie set
- Attacker controls: `shop`, `host`, the OAuth state cookie — specifically an OAuth begin where the encrypted state cookie can be fixed/replayed to bind a victim to an attacker state.
- Exploit idea: Encode the exact malicious input as a fixture so a future refactor can't silently reopen the hole.
- Invariant to test: OAuth state/nonce must be unpredictable and bound to this browser only
- Expected Immunefi impact: OAuth CSRF / authorization-code interception (account takeover, not plain logout CSRF) (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: regression fixture pinning the malicious input to a rejected outcome for CI.
