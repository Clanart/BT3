# Q1086: sanitize_shop_domain: A shop value with uppercase/mixed case `Shop.MyShopify.CoM` tha...

## Question
Can an unprivileged attacker (the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form) reach `sanitize_shop_domain / uri_from_shop_domain` in lib/shopify_app/utils.rb via any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages), supplying a shop value with uppercase/mixed case `Shop.MyShopify.CoM` that only `.downcase.strip` normalizes, so that nil return must fail closed, never fall through to a default/wildcard shop is violated, leading to authentication bypass into an unintended shop context? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/utils.rb — `sanitize_shop_domain / uri_from_shop_domain`
- Entrypoint: any route taking a ?shop= param (GET /login, GET /auth/shopify/callback, embedded pages)
- Attacker controls: the raw `shop` string and its casing, scheme, path, port, userinfo and unicode form — specifically a shop value with uppercase/mixed case `Shop.MyShopify.CoM` that only `.downcase.strip` normalizes.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: nil return must fail closed, never fall through to a default/wildcard shop
- Expected Immunefi impact: authentication bypass into an unintended shop context (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
