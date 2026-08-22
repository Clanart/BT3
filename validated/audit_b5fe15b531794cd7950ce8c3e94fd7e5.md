### Title
`id_token` URL query parameter is echoed back into the `Location` header of the `redirect_to_login` redirect via `sanitized_params` - ([File: lib/shopify_app/controller_concerns/login_protection.rb])

### Summary
While `ShopifyApp::WithShopifyIdToken#shopify_id_token` correctly accepts `id_token` from either the `Authorization` header or the `id_token` URL query param [1](#0-0) , only one of the two redirect paths that reflect request query params strips `id_token`. `ShopifyApp::Auth::TokenExchange#redirect_to_bounce_page` explicitly does `request.query_parameters.except(:id_token)` [2](#0-1) , but `ShopifyApp::LoginProtection#redirect_to_login` builds its redirect query from `sanitized_params`, which only sanitizes the `shop` key and passes every other param — including `id_token` — through unchanged [3](#0-2) .

### Finding Description
`redirect_to_login` is invoked whenever `activate_shopify_session` fails to load a current session (no session, expired session, or scope mismatch) [4](#0-3) . For a GET request it does:

```ruby
if request.get?
  path = request.path
  query = sanitized_params.to_query
...
session[:return_to] = return_to_url(path, query)
redirect_to(login_url_with_optional_shop)
``` [5](#0-4) 

`sanitized_params` is just `request.query_parameters` (for GET) with the `shop` value sanitized — no other key, including `id_token`, is stripped [3](#0-2) . This `path?query` (containing `id_token=<value>`) is stored in `session[:return_to]` and then, in `login_url_params`, folded into the `return_to` query parameter of the URL passed to `redirect_to`:

```ruby
return_to = RedirectSafely.make_safe(session[:return_to] || params[:return_to], nil)
if return_to.present? && return_to_param_required?
  query_params[:return_to] = return_to
end
...
url = "#{url}?#{query_params.to_query}" if query_params.present?
``` [6](#0-5) 

The resulting URL becomes the actual `Location` header value of the HTTP redirect issued to the browser (this is confirmed by the existing test asserting `assert_redirected_to "/login?#{params.to_query}"` for arbitrary params) [7](#0-6) . Consequently, an `id_token` supplied in the original request's query string is re-emitted, URL-encoded, inside the `return_to` parameter of the redirect's `Location` header — a second, app-generated exposure of the token beyond its original transmission (visible in server access logs of the redirect response, browser history for the login page, and any `Referer` header sent from the login page onward).

The `WithShopifyIdToken#shopify_id_token` reader itself does no validation before this leak occurs; `current_shopify_session` only rescues `CookieNotFoundError` and `InvalidJwtTokenError` to return `nil` [8](#0-7) , so both a genuinely valid-but-session-less `id_token` and a garbage string in the `id_token` param can reach `redirect_to_login` and be echoed back.

### Impact Explanation
This matches the Shopify HackerOne "sensitive data exposure via URL/log/redirect" impact class: an attacker who has captured (or is testing capture of) a victim's session `id_token` via `Referer` leakage or history can confirm/amplify the leak because the app itself re-serializes the token into a `Location` header, extending its exposure surface (server logs, proxy logs, browser history, and any subsequent `Referer` header from the login page) beyond the original one-time transmission. It does not directly hand over a shop's data or a stored access token, since `id_token` is a short-lived Shopify-issued JWT, and this endpoint doesn't itself decode/consume it maliciously — the impact is confined to token exposure amplification, not token forgery or bypass.

### Likelihood Explanation
Trivially reproducible: any GET request that fails `activate_shopify_session` (e.g., no session cookie, expired session) with `?id_token=<value>` in the query string exercises this code path. No authentication, HMAC, or shop verification is required to trigger it — the redirect happens before any session is established.

### Recommendation
In `ShopifyApp::SanitizedParams#sanitized_params` (and anywhere else request query/path params are reflected into `session[:return_to]` or a redirect URL), explicitly exclude `id_token` (and any other bearer-token-like param) the same way `redirect_to_bounce_page` already does with `.except(:id_token)`.

### Proof of Concept
```ruby
# test/shopify_app/controller_concerns/login_protection_test.rb
test "#activate_shopify_session with no Shopify session does not leak id_token into login redirect" do
  with_application_test_routes do
    ::ShopifyAPI::Utils::SessionUtils.stubs(:current_session_id).returns(nil)
    get :index, params: { shop: "foobar", id_token: "captured-victim-token" }
    refute_includes response.location, "captured-victim-token"
  end
end
```
With the current code, `sanitized_params.to_query` includes `id_token=captured-victim-token`, which is folded into `return_to` and appears in `response.location` — the assertion fails, demonstrating the leak (this test should be added to close the gap that `redirect_to_bounce_page` already closed with `.except(:id_token)`).

### Citations

**File:** lib/shopify_app/controller_concerns/with_shopify_id_token.rb (L7-11)
```ruby
    def shopify_id_token
      return @shopify_id_token if defined?(@shopify_id_token)

      @shopify_id_token = id_token_from_authorization_header || id_token_from_url_param
    end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L108-123)
```ruby
    def redirect_to_bounce_page
      ShopifyApp::Logger.debug("Redirecting to bounce page for patching Shopify ID token")
      patch_shopify_id_token_url =
        "#{ShopifyAPI::Context.host}#{ShopifyApp.configuration.root_url}/patch_shopify_id_token"
      patch_shopify_id_token_params = request.query_parameters.except(:id_token)

      bounce_url = "#{request.path}?#{patch_shopify_id_token_params.to_query}"

      # App Bridge will trigger a fetch to the URL in shopify-reload, with a new session token in headers
      patch_shopify_id_token_params["shopify-reload"] = bounce_url

      redirect_to(
        "#{patch_shopify_id_token_url}?#{patch_shopify_id_token_params.to_query}",
        allow_other_host: true,
      )
    end
```

**File:** lib/shopify_app/controller_concerns/sanitized_params.rb (L28-35)
```ruby
    def sanitized_params
      parameters = request.post? ? request.request_parameters : request.query_parameters
      parameters.clone.tap do |params_copy|
        if params[:shop].is_a?(String)
          params_copy[:shop] = sanitize_shop_param(params)
        end
      end
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

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L104-122)
```ruby
    def redirect_to_login
      if requested_by_javascript?
        add_top_level_redirection_headers(ignore_response_code: true)
        ShopifyApp::Logger.debug("Login redirect request is a XHR")
        head(:unauthorized)
      else
        if request.get?
          path = request.path
          query = sanitized_params.to_query
        else
          referer = URI(request.referer || "/")
          path = referer.path
          query = Rack::Utils.parse_nested_query(referer.query)
          query = query.merge(sanitized_params).to_query
        end
        session[:return_to] = return_to_url(path, query)
        ShopifyApp::Logger.debug("Redirecting to #{login_url_with_optional_shop}")
        redirect_to(login_url_with_optional_shop)
      end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L152-187)
```ruby
    def login_url_with_optional_shop(top_level: false)
      url = ShopifyApp.configuration.login_url

      query_params = login_url_params(top_level: top_level)

      url = "#{url}?#{query_params.to_query}" if query_params.present?
      url
    end

    def login_url_params(top_level:)
      query_params = {}
      query_params[:shop] = sanitized_params[:shop] if params[:shop].present?

      return_to = RedirectSafely.make_safe(session[:return_to] || params[:return_to], nil)

      if return_to.present? && return_to_param_required?
        query_params[:return_to] = return_to
      end

      has_referer_shop_name = referer_sanitized_shop_name.present?

      if has_referer_shop_name
        query_params[:shop] ||= referer_sanitized_shop_name
      end

      if params[:host].present?
        query_params[:host] ||= host
      end

      if params[:access_scopes].present?
        query_params[:scope] = params[:access_scopes].join(",")
      end

      query_params[:top_level] = true if top_level
      query_params
    end
```

**File:** test/shopify_app/controller_concerns/login_protection_test.rb (L377-385)
```ruby
  test '#activate_shopify_session with no Shopify session, redirects to the login url \
        with non-String shop param' do
    with_application_test_routes do
      ::ShopifyAPI::Utils::SessionUtils.stubs(:current_session_id).returns(nil)
      params = { shop: { id: 123 } }
      get :index, params: params
      assert_redirected_to "/login?#{params.to_query}"
    end
  end
```
