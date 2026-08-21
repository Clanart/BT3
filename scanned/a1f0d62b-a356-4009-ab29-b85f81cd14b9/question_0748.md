# Q0748: safe_embedded_app_url: A base64 `host` decoding to `victim.myshopify.com.attacker.com`...

## Question
Can an unprivileged attacker (the base64 `host` param, or the `shop` param used to synthesize host) reach `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?` in lib/shopify_app/controller_concerns/embedded_app.rb via GET a page that redirects to the embedded admin (invalid-token path, ensure_installed), supplying a base64 `host` decoding to `victim.myshopify.com.attacker.com` to defeat `sanitize_shop_domain` inside the guard, so that phishing-host detection must reject any non-trusted authority including userinfo tricks is violated, leading to phishing redirect / OAuth-parameter leak? Specifically confirm that the error/rescue branch fails closed and leaks no token or secret.

## Target
- File/function: lib/shopify_app/controller_concerns/embedded_app.rb — `safe_embedded_app_url / deduced_phishing_attack? / unsafe_embedded_host?`
- Entrypoint: GET a page that redirects to the embedded admin (invalid-token path, ensure_installed)
- Attacker controls: the base64 `host` param, or the `shop` param used to synthesize host — specifically a base64 `host` decoding to `victim.myshopify.com.attacker.com` to defeat `sanitize_shop_domain` inside the guard.
- Exploit idea: Force the rescued/exception branch and confirm it fails closed without leaking secrets or granting a session.
- Invariant to test: phishing-host detection must reject any non-trusted authority including userinfo tricks
- Expected Immunefi impact: phishing redirect / OAuth-parameter leak (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: error-injection test asserting the rescue branch yields no session, no token, and no secret in the response/log.
