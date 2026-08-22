No vulnerability found for this question.

**Analysis:**

Tracing all call sites of `SessionRepository.retrieve_shop_session_by_shopify_domain`:

1. **`LoginProtection#activate_shopify_session`** (the method that actually calls `ShopifyAPI::Context.activate_session`) uses `current_shopify_session`, which is loaded via `load_current_session` → `ShopifyAPI::Utils::SessionUtils.current_session_id(shopify_id_token, cookies, is_online)` → `SessionRepository.load_session(session_id)`. This session id is derived from the verified ID token/cookie, not from `params[:shop]`. <cite repo="Kirstentat/shopify_app--024" path="lib/shopify_app/controller_concerns/login_protection.rb" start="53="68" /> [1](#0-0) 

2. **`LoginProtection#shop_session`** (private, uses unverified `params[:shop]` via `retrieve_shop_session_by_shopify_domain`) is only used internally by `user_session_expected?` to decide whether an online token is expected — it is never passed to `ShopifyAPI::Context.activate_session` nor exposed in any response/header. [2](#0-1) 

3. **`EnsureInstalled#installed_shop_session`** does resolve a session purely from the unverified `params[:shop]` and is used in `validate_non_embedded_session` to make an Admin API call, but this call's response is never exposed to the caller (only a 401 triggers a redirect to re-auth; other errors are simply raised) — there is no data or token exposure to the requester. [3](#0-2) [4](#0-3) 

Critically, `EnsureInstalled` is explicitly and prominently documented as **not authenticating the request**, with an explicit warning not to use `installed_shop_session` for any action that accesses shop data or makes Shopify API calls — developers are directed to use `EnsureHasSession` for that purpose instead. [5](#0-4) [6](#0-5) 

Since the design intentionally separates the authenticated path (`EnsureHasSession`/`LoginProtection`, which derives the session strictly from a verified ID token/cookie before calling `ShopifyAPI::Context.activate_session`) from the unauthenticated, explicitly-documented-as-such `EnsureInstalled` path (which never activates a session or exposes token/data based on the unverified `shop` param), there's no code path meeting the exploit criteria in this question.

### Citations

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L255-268)
```ruby
    def shop_session
      ShopifyApp::SessionRepository.retrieve_shop_session_by_shopify_domain(sanitize_shop_param(params))
    end

    def online_token_configured?
      ShopifyApp.configuration.online_token_configured?
    end

    def user_session_expected?
      return false if shop_session.nil?
      return false if ShopifyApp.configuration.shop_access_scopes_strategy.update_access_scopes?(shop_session.shop)

      online_token_configured?
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L270-277)
```ruby
    def load_current_session(shopify_id_token: nil, cookies: nil, is_online: false)
      return ShopifyAPI::Context.load_private_session if ShopifyAPI::Context.private?

      session_id = ShopifyAPI::Utils::SessionUtils.current_session_id(shopify_id_token, cookies, is_online)
      return unless session_id

      ShopifyApp::SessionRepository.load_session(session_id)
    end
```

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L40-42)
```ruby
    def installed_shop_session
      @installed_shop_session ||= SessionRepository.retrieve_shop_session_by_shopify_domain(current_shopify_domain)
    end
```

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L73-82)
```ruby
    def validate_non_embedded_session
      return if loaded_directly_from_admin?

      client = ShopifyAPI::Clients::Rest::Admin.new(session: installed_shop_session)
      client.get(path: "shop")
    rescue ShopifyAPI::Errors::HttpResponseError => error
      ShopifyApp::Logger.info("Shop offline session no longer valid. Redirecting to OAuth install")
      redirect_to(shop_login) if error.code == 401
      raise error if error.code != 401
    end
```

**File:** docs/shopify_app/sessions.md (L168-182)
```markdown
##### `EnsureInstalled` — Installation Check Only
Use [EnsureInstalled](https://github.com/Shopify/shopify_app/blob/main/app/controllers/concerns/shopify_app/ensure_installed.rb) only for unauthenticated entry points, such as serving your embedded app's frontend shell. This concern checks whether the app is installed on the shop provided in the `shop` query string parameter. If the app is not installed, the request is redirected to login or the `embedded_redirect_url`.

> ⚠️ **This concern does not authenticate the request.** The `installed_shop_session` helper resolves the session from the user-controllable `shop` query parameter — it does not verify who is making the request. Do not use `EnsureInstalled` or `installed_shop_session` for any action that accesses shop data or makes Shopify API calls. Use `EnsureHasSession` instead.

- Example: serving the app frontend (no API calls)
```ruby
class HomeController < ApplicationController
  include ShopifyApp::EnsureInstalled

  def index
    # Serve the app shell — no API calls here
    render :index
  end
end
```

**File:** docs/shopify_app/controller-concerns.md (L28-33)
```markdown
## EnsureInstalled — Installation Check Only
Use this concern to verify that the app has been installed on a given shop. It is designed for unauthenticated entry points in embedded apps, such as serving the app shell or redirecting to OAuth.

> ⚠️ **This concern does not authenticate the request.** The shop is resolved from the `shop` query string parameter, which is user-controllable. Do not use this concern to gate access to shop data, access tokens, or Shopify API calls. For authenticated actions, use `EnsureHasSession`.

If the app is not installed for the provided `shop` parameter, the request will be redirected to login or the `embedded_redirect_url`.
```
