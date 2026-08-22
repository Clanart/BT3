### Title
`reauth_on_access_scope_changes` is never enforced when apps use the `TokenExchange` authentication strategy - ([File: lib/shopify_app/controller_concerns/token_exchange.rb])

### Summary
`ShopifyApp.configuration.reauth_on_access_scope_changes` is documented as a security control that forces a merchant to re-authorize the app via OAuth whenever the granted access scopes no longer match what the session holds, preventing a stale/over-privileged (or under-privileged, scope-mismatched) session from continuing to be used. The check that implements this is only present in `ShopifyApp::LoginProtection#activate_shopify_session`, not in `ShopifyApp::TokenExchange#activate_shopify_session`, which is the concern used by embedded apps on the modern "token exchange" / session-token auth strategy (`use_new_embedded_auth_strategy?`). As a result, any app built on the recommended, currently-promoted embedded auth flow silently loses this protection even when the developer explicitly enables `reauth_on_access_scope_changes = true`, exactly mirroring the reported bug class: a security invariant ("no privileged action should happen unless X is validated") that is enforced in one code path but silently skipped in a sibling code path serving the same purpose.

### Finding Description
`LoginProtection#activate_shopify_session` enforces three checks before activating a session and yielding to the controller action: [1](#0-0) 

Note the third check:
```ruby
if ShopifyApp.configuration.reauth_on_access_scope_changes &&
    !ShopifyApp.configuration.user_access_scopes_strategy.covers_scopes?(current_shopify_session)
  clear_shopify_session
  return redirect_to_login
end
```

`TokenExchange#activate_shopify_session`, used by controllers on the newer embedded-auth strategy (`EnsureInstalled` switches to `include ShopifyApp::TokenExchange` when `ShopifyApp.configuration.use_new_embedded_auth_strategy?` is true), performs a completely different, narrower set of checks: [2](#0-1) 

It only re-fetches a session when it is blank or expired (`should_exchange_expired_token?`), and rejects mismatched shop domains — it never calls `covers_scopes?` or consults `reauth_on_access_scope_changes` at all. Confirmed by searching the whole repo: `reauth_on_access_scope_changes` and `covers_scopes?`/`user_access_scopes_strategy` are referenced only in `lib/shopify_app/configuration.rb`, `login_protection.rb`, and `callback_controller.rb` — never in `token_exchange.rb`.

`EnsureInstalled` wires `TokenExchange` in as the active session-activation concern for the new strategy: [3](#0-2) 

The documentation for this config flag promises that "the app will automatically request new scopes from merchants... To enable your app to reauth via OAuth on scope changes, you can set `config.reauth_on_access_scope_changes = true`," with no caveat that this only applies to apps using `LoginProtection` and not `TokenExchange`.

### Impact Explanation
If a merchant reduces the granted access scopes (revokes a scope) after the app previously obtained a broader session, an app using the modern token-exchange embedded strategy will continue treating the existing (now over-broad relative to what was actually granted, or simply stale) session as valid without forcing re-authorization, because the scope-coverage check that `LoginProtection` performs is entirely absent from `TokenExchange`. This defeats a merchant/developer-configured security control (`reauth_on_access_scope_changes`) intended to keep session privileges synchronized with the currently granted scopes, allowing continued use of a session whose scope state has silently diverged from what the merchant authorized — a scope/authorization-state check bypass analogous to the veALCX cooldown check being enforced in one function (`vote`) but omitted in another (`claim`).

### Likelihood Explanation
Any embedded app that (a) opts into `use_new_embedded_auth_strategy?` (the path Shopify is steering apps toward) and (b) sets `reauth_on_access_scope_changes = true` expecting the documented protection is silently unprotected — there's no additional attacker action needed beyond a normal scope-change event on an already-authenticated shop/user session, making this reachable through completely standard, unprivileged embedded-app request flows once the configuration is enabled.

### Recommendation
Add the same `reauth_on_access_scope_changes` / `user_access_scopes_strategy.covers_scopes?` check inside `TokenExchange#activate_shopify_session` (or a shared helper reused by both concerns), forcing a token-exchange re-fetch / re-authorization when scopes no longer match, so the behavior is consistent regardless of which embedded-auth strategy a controller uses.

### Proof of Concept
Conceptual PoC (would need to be run in the gem's test suite, analogous to `test/shopify_app/controller_concerns/login_protection_test.rb` case for `reauth_on_access_scope_changes`):
1. Configure `ShopifyApp.configuration.reauth_on_access_scope_changes = true` and `use_new_embedded_auth_strategy? => true`.
2. Store a session for a shop whose granted scopes no longer cover `ShopifyApp.configuration.scope` (simulate a scope reduction), e.g. via `ShopifyApp::SessionRepository.store_session`.
3. Hit a controller that includes `ShopifyApp::EnsureInstalled` (which includes `ShopifyApp::TokenExchange` under the new strategy).
4. Observe: the request succeeds and yields to the controller action without any redirect to login/reauth, whereas the equivalent test in `test/shopify_app/controller_concerns/login_protection_test.rb` for `LoginProtection` shows a redirect to `/login` under identical scope-mismatch conditions — demonstrating the two concerns diverge on enforcement of the same documented security control.

### Citations

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L24-41)
```ruby
    def activate_shopify_session
      if current_shopify_session.blank?
        signal_access_token_required
        ShopifyApp::Logger.debug("No session found, redirecting to login")
        return redirect_to_login
      end

      if ShopifyApp.configuration.check_session_expiry_date && current_shopify_session.expired?
        ShopifyApp::Logger.debug("Session expired, redirecting to login")
        clear_shopify_session
        return redirect_to_login
      end

      if ShopifyApp.configuration.reauth_on_access_scope_changes &&
          !ShopifyApp.configuration.user_access_scopes_strategy.covers_scopes?(current_shopify_session)
        clear_shopify_session
        return redirect_to_login
      end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L19-32)
```ruby
    def activate_shopify_session(&block)
      retrieve_session_from_token_exchange if current_shopify_session.blank? || should_exchange_expired_token?

      return if reject_mismatched_requested_shopify_domain

      ShopifyApp::Logger.debug("Activating Shopify session")
      ShopifyAPI::Context.activate_session(current_shopify_session)
      with_token_refetch(current_shopify_session, shopify_id_token, &block)
    rescue *INVALID_SHOPIFY_ID_TOKEN_ERRORS => e
      respond_to_invalid_shopify_id_token(e)
    ensure
      ShopifyApp::Logger.debug("Deactivating session")
      ShopifyAPI::Context.deactivate_session
    end
```

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L18-27)
```ruby
      before_action :check_shop_domain

      if ShopifyApp.configuration.use_new_embedded_auth_strategy?
        include ShopifyApp::TokenExchange
        around_action :activate_shopify_session
      else
        before_action :check_shop_known
        before_action :validate_non_embedded_session
      end
    end
```
