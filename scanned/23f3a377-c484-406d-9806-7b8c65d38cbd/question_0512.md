# Q0512: sanitize_shop_domain: A shop value using a trailing dot FQDN `shop.myshopify.com.` to...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value using a trailing dot FQDN `shop.myshopify.com.` to dodge the `uri.domain == trusted_domain` check, so that a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted is violated, leading to open redirect / OAuth-token leak to an attacker-controlled origin? Specifically confirm that no protected state is reachable by skipping or repeating an auth step.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value using a trailing dot FQDN `shop.myshopify.com.` to dodge the `uri.domain == trusted_domain` check.
- Exploit idea: Walk the auth state machine out of order (skip/repeat a step) and confirm no step can be reached unauthenticated.
- Invariant to test: a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted
- Expected Immunefi impact: open redirect / OAuth-token leak to an attacker-controlled origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: state-machine test exercising out-of-order transitions and asserting each protected state still requires verification.
