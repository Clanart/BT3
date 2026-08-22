### Title
Access-scope reauthentication (emergency-pause-equivalent) enforcement is missing from the Token Exchange authentication path - ([File: lib/shopify_app/controller_concerns/token_exchange.rb])

### Summary
`reauth_on_access_scope_changes` is the mechanism this gem uses to force a session to be invalidated ("paused") once it no longer covers the scopes an app requires — analogous to the `setEmergencyPaused`/`whenNotPaused` control in the referenced report. It is enforced in the legacy OAuth/cookie session path (`ShopifyApp::LoginProtection`), but the enforcement check is absent from the newer, now-default embedded-app Token Exchange path (`ShopifyApp::TokenExchange`), which is the path exercised whenever `config.new_embedded_auth_strategy` is enabled.

### Finding Description
In `ShopifyApp::LoginProtection#activate_shopify_session`, before a session is activated the gem explicitly re-checks scope coverage and forces re-login if it fails: [1](#0-0) 

This is the "pause" analog: it forces the stale/under-scoped session to be cleared and the request redirected to login, rather than letting the request proceed with an access token that no longer matches the shop's granted permission set.

`ShopifyApp::TokenExchange#activate_shopify_session`, which is the equivalent method used for embedded apps using the new auth strategy (`EnsureHasSession` swaps `LoginProtection`+`around_action :activate_shopify_session` for `TokenExchange`+`around_action :activate_shopify_session` when `use_new_embedded_auth_strategy?` is true), performs none of this scope validation: [2](#0-1) 

It only re-exchanges the token when the session is blank or expired (`should_exchange_expired_token?`, gated by `check_session_expiry_date`): [3](#0-2) 

There is no call to `ShopifyApp.configuration.user_access_scopes_strategy.covers_scopes?` anywhere in `token_exchange.rb`, even though the configuration explicitly supports `reauth_on_access_scope_changes` and ships `ShopifyApp::AccessScopes::ShopStrategy`/`UserStrategy` for exactly this purpose: [4](#0-3) 

`EnsureHasSession` wires this up per auth strategy: [5](#0-4) 

### Impact Explanation
For an app configured with `reauth_on_access_scope_changes = true` (an app-owner opt-in security control meant to guarantee sessions are re-validated whenever a merchant's granted scopes stop covering the app's required scopes — e.g., after a merchant reduces permissions or after a scope downgrade), the control is silently bypassed for any app using the modern Token Exchange embedded-auth flow (`new_embedded_auth_strategy = true`, the currently recommended and increasingly default configuration for embedded apps). A stale/offline session whose access token no longer corresponds to the shop's actual granted scopes will continue to be accepted and activated (`ShopifyAPI::Context.activate_session(current_shopify_session)`) for every subsequent request, producing continued authenticated API access on scopes the merchant believes were revoked. This is a direct analog of the reported issue: a documented, opt-in "stop using this session" control exists in the codebase, but one of the two live enforcement code paths never applies it.

### Likelihood Explanation
Likelihood is moderate-to-high for any embedded app that (a) has opted into `reauth_on_access_scope_changes` for security compliance and (b) has also adopted the new Token Exchange strategy — which is the direction Shopify is pushing all embedded apps toward. No attacker action beyond normal authenticated requests through the standard embedded-app request flow is required; the gap is purely a missing enforcement call in a first-party, widely used gem code path.

### Recommendation
Add an equivalent access-scope coverage check inside `ShopifyApp::TokenExchange#activate_shopify_session` (mirroring `LoginProtection`), e.g. clear/refresh the session and force re-exchange when `ShopifyApp.configuration.reauth_on_access_scope_changes` is true and `!ShopifyApp.configuration.user_access_scopes_strategy.covers_scopes?(current_shopify_session)`, before activating the session and yielding to the controller action.

### Proof of Concept
1. Configure the app with `config.new_embedded_auth_strategy = true` and `config.reauth_on_access_scope_changes = true`.
2. Merchant reduces granted access scopes on the shop (or the app's required `scope` config is expanded) such that the previously stored offline/online session no longer covers required scopes.
3. A subsequent embedded-app request goes through `EnsureHasSession` → `ShopifyApp::TokenExchange#activate_shopify_session`.
4. Because `current_shopify_session` is present and not expired, `retrieve_session_from_token_exchange` is skipped; no scope-coverage check exists in this module, so `ShopifyAPI::Context.activate_session(current_shopify_session)` runs and the controller action executes using the outdated-scope session — unlike the identical scenario under the legacy `LoginProtection` path, which would clear the session and redirect to login instead.

### Citations

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L37-41)
```ruby
      if ShopifyApp.configuration.reauth_on_access_scope_changes &&
          !ShopifyApp.configuration.user_access_scopes_strategy.covers_scopes?(current_shopify_session)
        clear_shopify_session
        return redirect_to_login
      end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L19-36)
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

    def should_exchange_expired_token?
      ShopifyApp.configuration.check_session_expiry_date && current_shopify_session.expired?
    end
```

**File:** lib/shopify_app/configuration.rb (L92-112)
```ruby
    def shop_access_scopes_strategy
      return ShopifyApp::AccessScopes::NoopStrategy unless reauth_on_access_scope_changes

      ShopifyApp::AccessScopes::ShopStrategy
    end

    def user_access_scopes_strategy=(class_name)
      unless class_name.is_a?(String)
        raise ConfigurationError, "Invalid user access scopes strategy - expected a string"
      end

      @user_access_scopes_strategy = class_name.safe_constantize
    end

    def user_access_scopes_strategy
      return @user_access_scopes_strategy if @user_access_scopes_strategy

      return ShopifyApp::AccessScopes::NoopStrategy unless reauth_on_access_scope_changes

      ShopifyApp::AccessScopes::UserStrategy
    end
```

**File:** app/controllers/concerns/shopify_app/ensure_has_session.rb (L7-18)
```ruby
    included do
      include ShopifyApp::Localization

      if ShopifyApp.configuration.use_new_embedded_auth_strategy?
        include ShopifyApp::TokenExchange
        around_action :activate_shopify_session
      else
        include ShopifyApp::LoginProtection
        before_action :login_again_if_different_user_or_shop
        around_action :activate_shopify_session
        after_action :add_top_level_redirection_headers
      end
```
