Based on the evidence gathered, `ShopifyApp::SessionsController#destroy` (the logout endpoint) only clears the Rails session hash — it never clears the `SESSION_COOKIE_NAME` encrypted cookie that `current_shopify_session` relies on to reconstitute an authenticated session.

### Title
Logout (`SessionsController#destroy`) fails to clear the Shopify session cookie, leaving the previous session usable after "logout" - (File: `app/controllers/shopify_app/sessions_controller.rb`)

### Summary
The external report's bug class is: a "revoke/abdicate" action removes the primary credential (`gov`) but forgets to reset an auxiliary piece of state (`pendingGov`), so the old, supposedly-revoked actor can still complete a privileged action (`acceptGov`) afterward. The reachable analog in `shopify_app` is `SessionsController#destroy`, the logout action, which resets the Rails session but does not clear the encrypted session cookie that `current_shopify_session` uses to reload an active, authenticated Shopify session.

### Finding Description
`destroy` only calls `reset_session` and redirects: [1](#0-0) 

Elsewhere in the same concern hierarchy (`ShopifyApp::LoginProtection`, included by `SessionsController`), the canonical way to invalidate the session-bearing state is `clear_shopify_session`, which clears the *encrypted cookie* keyed by `ShopifyAPI::Auth::Oauth::SessionCookie::SESSION_COOKIE_NAME`: [2](#0-1) 

That cookie — not the Rails `session[:shopify]` hash — is what `current_shopify_session` actually uses to reload/re-derive an authenticated session on every subsequent request: [3](#0-2) 

`destroy` clears `session[:shopify]`, `session[:shopify_domain]`, `session[:shopify_user]`, etc. (Rails session keys), but it never calls `clear_shopify_session`, so the `SESSION_COOKIE_NAME` encrypted cookie set during OAuth/token-exchange remains present in the browser after "logout." Every other place in the codebase that legitimately invalidates a session (expired session, scope mismatch, HTTP 401 rescue, shop/user mismatch) explicitly calls `clear_shopify_session` before redirecting to login: [4](#0-3) [5](#0-4) [6](#0-5) 

This is the same bug class as `__abdicate()`: the "primary" state (`gov` / Rails `session`) is cleared, but the auxiliary state that still grants access (`pendingGov` / the encrypted session cookie) is left untouched, so the previously-authenticated actor is not actually revoked.

### Impact Explanation
On a shared or public device, a merchant/staff user who clicks "Log out" (hitting `SessionsController#destroy`) is shown the "logged out" flash and redirected to the login page, giving the false impression that the app session has been revoked. However, since the encrypted cookie is untouched, a subsequent visitor to the same browser who navigates to any authenticated route can have `current_shopify_session` resolve to the still-valid prior session (as long as the underlying access token in server-side `SessionRepository` storage hasn't separately expired/been revoked), effectively continuing to act as the previous, "logged out" user. This is a session/token persistence-after-logout issue — the same trust-boundary failure class as the reported bug (revoked identity retains usable credential material).

### Likelihood Explanation
This is trivially reachable by any unprivileged party with browser access after a legitimate user's logout action: no secrets, no privileged keys, and no special conditions are required — only the standard `GET /logout` flow that every app using the gem's default `SessionsController` exposes.

### Proof of Concept
1. Merchant logs into the embedded/non-embedded app; OAuth/token-exchange completes and the browser receives the encrypted cookie `ShopifyAPI::Auth::Oauth::SessionCookie::SESSION_COOKIE_NAME`.
2. Merchant clicks "logout," hitting `GET /logout` → `SessionsController#destroy`, which calls `reset_session` only (`app/controllers/shopify_app/sessions_controller.rb#L31-L37`).
3. Inspect the browser cookie jar: the `SESSION_COOKIE_NAME` cookie is still present (it was never cleared, unlike in `clear_shopify_session`).
4. Navigate to any controller action that calls `activate_shopify_session`/`current_shopify_session`; because `current_shopify_session` derives the session from the cookie (`lib/shopify_app/controller_concerns/login_protection.rb#L53-L68`), the "logged out" session is reconstituted and the request proceeds as authenticated.

### Citations

**File:** app/controllers/shopify_app/sessions_controller.rb (L31-37)
```ruby
    def destroy
      reset_session
      flash[:notice] = I18n.t(".logged_out")
      ShopifyApp::Logger.debug("Session destroyed")
      ShopifyApp::Logger.debug("Redirecting to #{login_url_with_optional_shop}")
      redirect_to(login_url_with_optional_shop)
    end
```

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

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L53-68)
```ruby
    def current_shopify_session
      @current_shopify_session ||= begin
        cookie_name = ShopifyAPI::Auth::Oauth::SessionCookie::SESSION_COOKIE_NAME
        load_current_session(
          shopify_id_token: shopify_id_token,
          cookies: { cookie_name => cookies.encrypted[cookie_name] },
          is_online: online_token_configured?,
        )
      rescue ShopifyAPI::Errors::CookieNotFoundError
        ShopifyApp::Logger.warn("No cookies have been found - cookie name: #{cookie_name}")
        nil
      rescue ShopifyAPI::Errors::InvalidJwtTokenError
        ShopifyApp::Logger.warn("Invalid JWT token for current Shopify session")
        nil
      end
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L70-76)
```ruby
    def login_again_if_different_user_or_shop
      return unless session_id_conflicts_with_params || session_shop_conflicts_with_params

      ShopifyApp::Logger.debug("Clearing session and redirecting to login")
      clear_shopify_session
      redirect_to_login
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L132-138)
```ruby
    def close_session
      ShopifyApp::Logger.debug("Closing session")
      clear_shopify_session

      ShopifyApp::Logger.debug("Redirecting to login")
      redirect_to_login
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L148-150)
```ruby
    def clear_shopify_session
      cookies.encrypted[ShopifyAPI::Auth::Oauth::SessionCookie::SESSION_COOKIE_NAME] = nil
    end
```
