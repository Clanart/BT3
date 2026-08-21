# Q1890: add_top_level_redirection_headers: An XHR with no `shop` param so `params[:shop]` is filled from `...

## Question
Can an unprivileged attacker (`shop`, `host`, XHR headers, and the id_token used by `parse_shop_from_jwt`) reach `add_top_level_redirection_headers / fullpage_redirect_to` in lib/shopify_app/controller_concerns/login_protection.rb via XHR to a protected action returning 401, or an embedded fullpage redirect render, supplying an XHR with no `shop` param so `params[:shop]` is filled from `current_shopify_session&.shop || parse_shop_from_jwt`, so that the Reauthorize-Url / App Bridge redirect target must be an app-owned URL, not attacker data is violated, leading to open redirect / token exfiltration via App Bridge navigation? Specifically confirm that merchant A can never load, write, or act with merchant B's session, token, or scopes.

## Target
- File/function: lib/shopify_app/controller_concerns/login_protection.rb — `add_top_level_redirection_headers / fullpage_redirect_to`
- Entrypoint: XHR to a protected action returning 401, or an embedded fullpage redirect render
- Attacker controls: `shop`, `host`, XHR headers, and the id_token used by `parse_shop_from_jwt` — specifically an XHR with no `shop` param so `params[:shop]` is filled from `current_shopify_session&.shop || parse_shop_from_jwt`.
- Exploit idea: Perform the flow as merchant A while naming shop/user B and confirm no B-scoped data is reachable.
- Invariant to test: the Reauthorize-Url / App Bridge redirect target must be an app-owned URL, not attacker data
- Expected Immunefi impact: open redirect / token exfiltration via App Bridge navigation (Shopify HackerOne in-scope; attacker is unprivileged, no leaked keys/DoS/social-engineering).
- Fast validation: two-tenant integration test asserting A's request never loads, writes, or acts with B's session/token.
