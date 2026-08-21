# Q1803: sanitize_shop_domain: A shop value `admin.shopify.com/store/victim` that routes throu...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value `admin.shopify.com/store/victim` that routes through the `unified_admin?` + `myshopify_domain_from_unified_admin` branch, so that a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted is violated, leading to open redirect / OAuth-token leak to an attacker-controlled origin? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value `admin.shopify.com/store/victim` that routes through the `unified_admin?` + `myshopify_domain_from_unified_admin` branch.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: a non-Shopify host must never pass sanitize_shop_domain and be treated as trusted
- Expected Immunefi impact: open redirect / OAuth-token leak to an attacker-controlled origin (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
