### Title
`id_token` query parameter is echoed back unfiltered into the `redirect_to_login` `return_to` URL, leaking it via `Location` header/logs/Referer - ([File: lib/shopify_app/controller_concerns/login_protection.rb])

### Summary
`ShopifyApp::WithShopifyIdToken#id_token_from_url_param` reads `params["id_token"]` from the query string, and when authentication subsequently fails, `LoginProtection#redirect_to_login` builds the `return_to` value from `sanitized_params`, which does **not** strip the `id_token` key. As a result, whatever id_token value was on the incoming GET request (whether attacker-forged and rejected, or a legitimate victim token accompanying an expired/invalid session) is copied verbatim into the app's own redirect `Location` header to the Shopify `login_url`, exposing it to access logs, browser history, and Referer leakage on the next hop.

### Finding Description
- `id_token_from_url_param` simply returns `params["id_token"]` with no filtering: [1](#0-0) 
- `shopify_id_token` picks this up as a fallback when there's no `Authorization: Bearer` header: [2](#0-1) 
- `LoginProtection#current_shopify_session` feeds this value into `ShopifyAPI::Auth::JwtPayload`/session lookup, and on `InvalidJwtTokenError`/`CookieNotFoundError` or expiry it becomes `nil`, triggering `redirect_to_login`: [3](#0-2) [4](#0-3) 
- `redirect_to_login` builds `query` from `sanitized_params.to_query` for GET requests, then stores it as `session[:return_to]` and ultimately re-emits it in the `login_url_with_optional_shop` redirect: [5](#0-4) 
- `sanitized_params` clones `request.query_parameters`/`request.request_parameters` and only sanitizes the `:shop` key — it never removes `:id_token`: [6](#0-5) 
- This is inconsistent with how the same codebase's Token Exchange flow handles the same risk: `TokenExchange#redirect_to_bounce_page` explicitly does `request.query_parameters.except(:id_token)` before building its redirect URL, precisely to avoid leaking the token in a `Location` header: [7](#0-6)  and this exclusion is asserted by tests using a literal `"dont-include-this-id-token"` param value [8](#0-7) . `LoginProtection`/`sanitized_params` has no equivalent exclusion, so the protection present in one code path is missing in the other.

The existing checks (`sanitize_shop_domain`, JWT verification in `JwtPayload.new`) correctly reject a forged/invalid `id_token` for authentication purposes — that part of the invariant holds. What is not protected is the **confidentiality of the raw token value on the redirect path**: it is never stripped before being placed into the outbound `Location` header via `return_to`.

### Impact Explanation
This is a token/secret exposure via URL/log issue, not an authentication bypass. Concrete scoped impact: any legitimate `id_token` value that accompanies a request which fails the app's own session checks (expired shop session, cookie mismatch, or scope/expiry re-auth flows in `activate_shopify_session`) gets embedded into the app's redirect `Location` header URL, and from there into: (1) web/proxy access logs of the app server and any intermediate CDN/load balancer, (2) the browser's history, and (3) the `Referer` header of the next page the browser navigates to (the Shopify login/OAuth page). This maps to Shopify's "Sensitive Data Exposure" / session token leakage impact class; within the (typically short) `exp` window of the id_token this could enable a session token replay/hijack by anyone with access to those logs or the referrer chain.

### Likelihood Explanation
Preconditions: a controller including `LoginProtection` (most embedded-app controllers), a GET request carrying `id_token` as a URL param (an officially supported input path per the `WithShopifyIdToken` concern and CHANGELOG entry "Support `id_token` from URL param"), and a subsequent session-validation failure (expired session, scope change requiring reauth, cookie/session mismatch) that triggers `redirect_to_login`. This is a normal, easily reproducible flow (no attacker privilege needed) whenever an app's session becomes invalid while `id_token` is present in the URL — it doesn't require crafting a forged JWT, just observing/logging the resulting redirect.

### Recommendation
Strip `id_token` (and any other bearer/secret-like params) from `sanitized_params` in `lib/shopify_app/controller_concerns/sanitized_params.rb`, mirroring the `.except(:id_token)` pattern already used in `lib/shopify_app/controller_concerns/token_exchange.rb#redirect_to_bounce_page`, so it never flows into `return_to_url`/`session[:return_to]`/the `login_url` redirect `Location` header.

### Proof of Concept
Add to `test/shopify_app/controller_concerns/login_protection_test.rb`:
```ruby
test "#redirect_to_login does not leak id_token into the return_to redirect URL" do
  with_application_test_routes do
    ::ShopifyAPI::Utils::SessionUtils.stubs(:current_session_id).returns(nil)
    get :index, params: { shop: "foobar", id_token: "leaked.jwt.value" }

    refute_includes response.headers["Location"], "leaked.jwt.value"
    refute_includes session[:return_to], "leaked.jwt.value"
  end
end
```
Expected today: this test **fails** — `response.headers["Location"]` is `"/login?return_to=%2F%3Fid_token%3Dleaked.jwt.value%26shop%3Dfoobar.myshopify.com&shop=foobar.myshopify.com"` and `session[:return_to]` is `"/?id_token=leaked.jwt.value&shop=foobar.myshopify.com"`, confirming the raw token is present in both the stored session value and the outbound `Location` header.

### Citations

**File:** lib/shopify_app/controller_concerns/with_shopify_id_token.rb (L7-11)
```ruby
    def shopify_id_token
      return @shopify_id_token if defined?(@shopify_id_token)

      @shopify_id_token = id_token_from_authorization_header || id_token_from_url_param
    end
```

**File:** lib/shopify_app/controller_concerns/with_shopify_id_token.rb (L44-46)
```ruby
    def id_token_from_url_param
      params["id_token"]
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L24-35)
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

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L104-130)
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
    end

    # Apps which use cookies for session storage, and thus have a 4kB session data
    # limit, may choose to override this method to prevent excessively-long `query`
    # strings from causing a CookieOverflow error.
    def return_to_url(path, query)
      query.blank? ? path.to_s : "#{path}?#{query}"
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

**File:** test/shopify_app/controller_concerns/token_exchange_test.rb (L411-420)
```ruby
    test "Redirects to bounce page if Shopify ID token is invalid with #{invalid_shopify_id_token_error}" do
      ShopifyApp.configuration.root_url = "/my-root"
      ShopifyAPI::Utils::SessionUtils.stubs(:session_id_from_shopify_id_token).raises(invalid_shopify_id_token_error)
      request.headers["HTTP_AUTHORIZATION"] = nil

      params = { shop: @shop, my_param: "for-keeps", id_token: "dont-include-this-id-token", embedded: "1" }
      reload_url = CGI.escape("/reloaded_path?embedded=1&my_param=for-keeps&shop=#{@shop}")
      expected_redirect_url = "https://test.host/my-root/patch_shopify_id_token"\
        "?embedded=1&my_param=for-keeps&shop=#{@shop}"\
        "&shopify-reload=#{reload_url}"
```
