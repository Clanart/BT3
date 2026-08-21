# Q2220: add_top_level_redirection_headers: A `current_shopify_domain` of nil that raises `ShopifyDomainNot...

## Question
Can an unprivileged attacker (`shop`, `host`, XHR headers, and the id_token used by `parse_shop_from_jwt`) reach `add_top_level_redirection_headers / fullpage_redirect_to` in lib/shopify_app/controller_concerns/login_protection.rb via XHR to a protected action returning 401, or an embedded fullpage redirect render, supplying a `current_shopify_domain` of nil that raises `ShopifyDomainNotFound` inside `fullpage_redirect_to`, so that the Reauthorize-Url / App Bridge redirect target must be an app-owned URL, not attacker data is violated, leading to open redirect / token exfiltration via App Bridge navigation? Specifically confirm that the full public-route flow yields only a rejection or attacker-own-scope result.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `add_top_level_redirection_headers / fullpage_redirect_to`
- Entrypoint: XHR to a protected action returning 401, or an embedded fullpage redirect render
- Attacker controls: `shop`, `host`, XHR headers, and the id_token used by `parse_shop_from_jwt` — specifically a `current_shopify_domain` of nil that raises `ShopifyDomainNotFound` inside `fullpage_redirect_to`.
- Exploit idea: Run the full public route end-to-end with the crafted input and confirm the real-world outcome is safe.
- Invariant to test: the Reauthorize-Url / App Bridge redirect target must be an app-owned URL, not attacker data
- Expected Immunefi impact: open redirect / token exfiltration via App Bridge navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: end-to-end integration test through the real route asserting the attacker outcome is a reject/own-scope only.
