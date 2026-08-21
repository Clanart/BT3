# Q2558: add_top_level_redirection_headers: A JWT with an attacker `shop` claim consumed by `parse_shop_fro...

## Question
Can an unprivileged attacker (`shop`, `host`, XHR headers, and the id_token used by `parse_shop_from_jwt`) reach `add_top_level_redirection_headers / fullpage_redirect_to` in lib/shopify_app/controller_concerns/login_protection.rb via XHR to a protected action returning 401, or an embedded fullpage redirect render, supplying a JWT with an attacker `shop` claim consumed by `parse_shop_from_jwt` and echoed into the Reauthorize-Url header, so that the Reauthorize-Url / App Bridge redirect target must be an app-owned URL, not attacker data is violated, leading to open redirect / token exfiltration via App Bridge navigation? Specifically confirm that the security property holds for the whole generated input class, not just one example.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `add_top_level_redirection_headers / fullpage_redirect_to`
- Entrypoint: XHR to a protected action returning 401, or an embedded fullpage redirect render
- Attacker controls: `shop`, `host`, XHR headers, and the id_token used by `parse_shop_from_jwt` — specifically a JWT with an attacker `shop` claim consumed by `parse_shop_from_jwt` and echoed into the Reauthorize-Url header.
- Exploit idea: State the security property as an invariant and check it over a generated range of related inputs.
- Invariant to test: the Reauthorize-Url / App Bridge redirect target must be an app-owned URL, not attacker data
- Expected Immunefi impact: open redirect / token exfiltration via App Bridge navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: invariant/property test over generated inputs asserting the security property holds universally.
