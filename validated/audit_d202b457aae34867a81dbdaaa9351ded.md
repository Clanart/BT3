### Title
Unauthenticated attacker can trigger a live Shopify Admin API call using a named shop's offline access token before any token verification - ([File: app/controllers/concerns/shopify_app/ensure_installed.rb])

### Summary
`EnsureInstalled#validate_non_embedded_session` gates the Admin API call solely on `params[:embedded] == "1"` (via `loaded_directly_from_admin?`), not on any proof of caller identity. An anonymous request to a route including `ShopifyApp::EnsureInstalled` with `shop=<victim-shop>.myshopify.com` and no `embedded=1` will cause the app to instantiate `ShopifyAPI::Clients::Rest::Admin` with the victim's stored offline session and issue a live `GET shop` request using the victim's access token, purely because the requester named that shop.

### Finding Description
The `included do ... end` block wires up, for the non-token-exchange path, `before_action :check_shop_domain`, `before_action :check_shop_known`, `before_action :validate_non_embedded_session` [1](#0-0) . None of these three callbacks verify a signed/JWT session token, an HMAC, or any cookie tying the request to a legitimate Shopify Admin session — they only look at `params[:shop]` and `params[:embedded]`:

- `check_shop_domain` just requires `params[:shop]` to sanitize to a domain [2](#0-1) .
- `check_shop_known` loads `installed_shop_session` (`SessionRepository.retrieve_shop_session_by_shopify_domain(current_shopify_domain)`) purely from the attacker-supplied `shop` param, with no cross-check against a caller-proven identity [3](#0-2) .
- `validate_non_embedded_session` returns early only if `loaded_directly_from_admin?` is true, i.e. `ShopifyApp.configuration.embedded_app?` and `params[:embedded] == "1"` [4](#0-3) . If the attacker simply omits `embedded` or sends anything other than `"1"`, this guard is false, and the method proceeds to build `ShopifyAPI::Clients::Rest::Admin.new(session: installed_shop_session)` and call `client.get(path: "shop")` [5](#0-4) .

The existing test suite confirms this is exactly the code path exercised, with no token/HMAC/session-cookie check anywhere in between: a stubbed session and a bare `get :index, params: { shop: shopify_domain }` triggers `ShopifyAPI::Clients::Rest::Admin.new` and `client.get` unconditionally, and the only branch that skips the API call is when `params[:embedded]` is set to `"1"` [6](#0-5) [7](#0-6) . There is no `ActiveSupport::SecurityUtils.secure_compare`, no `ShopifyAPI::Utils::SessionUtils.session_id_from_shopify_id_token`, no HMAC/CSRF check anywhere in this concern to establish that the requester is actually operating within the named shop's authenticated Admin context. `installed_shop_session` is keyed only by the shop domain string the attacker supplies (after `ShopifyApp::Utils.sanitize_shop_domain`) [8](#0-7) , so any attacker who knows or guesses a victim's `*.myshopify.com` domain (these are often guessable/public) can trigger this.

### Impact Explanation
This matches Shopify's "unauthenticated triggering of outbound API calls using another shop's access token" and "session validity oracle" class: an attacker who can only name a shop domain forces the app server to make a real Admin REST API call authenticated with that shop's stored offline access token, without proving any relationship to that shop. Concrete consequences:
1. **Session/token-validity oracle** – the response behavior differs (`200`/redirect-to-login on `401`/raised error on other codes) depending on whether the shop's session is still valid, letting an attacker enumerate which shops have valid, revoked, or errored sessions.
2. **Unauthorized use of a victim's access token to make live outbound calls** to Shopify's Admin API on the attacker's timing/request cadence, contributing to rate-limit consumption and creating audit-log noise attributable to the victim shop, without the caller ever proving identity.

This does not directly return shop data to the attacker in this endpoint (the `GET shop` response isn't rendered back), but it is a genuine authentication-bypass of the "prove identity before using a shop's token" invariant, and it's a stepping stone/oracle usable in a broader compromise chain.

### Likelihood Explanation
High feasibility and repeatability: the only precondition is `use_new_embedded_auth_strategy?` is false (the classic/legacy install flow, still a supported and documented configuration) and that the victim shop has an installed (stored) offline session — both realistic, common conditions for existing shopify_app-based apps. The attacker needs zero secrets: no session token, no HMAC, no host param correctness — just a GET to any controller that includes `ShopifyApp::EnsureInstalled` with `shop=<victim>.myshopify.com` and `embedded` param omitted or not `"1"`. This is trivially repeatable per request/per shop.

### Recommendation
`validate_non_embedded_session` should not be reachable, or should not perform a real Admin API call, based solely on `params[:shop]`/`params[:embedded]`. Require independent proof of the caller's relationship to the shop before making the live API call — e.g., verify a Shopify-signed ID token/JWT (as done in the `use_new_embedded_auth_strategy?`/`TokenExchange` path), or gate this check behind an authenticated Rails session established during OAuth callback, rather than trusting an unauthenticated `shop` query parameter to select which shop's stored access token gets used for an outbound API call.

### Proof of Concept
Using the existing test harness pattern in `test/controllers/concerns/ensure_installed_test.rb`:

```ruby
test "unauthenticated caller can trigger a live Admin API call for a named shop without embedded=1" do
  session = mock
  ShopifyApp::SessionRepository.stubs(:retrieve_shop_session_by_shopify_domain).returns(session)

  client = mock
  # No JWT/session-token/HMAC verification occurs anywhere before this;
  # the attacker only supplies `shop`, and no `embedded=1`.
  ShopifyAPI::Clients::Rest::Admin.expects(:new).with(session: session).returns(client)
  client.expects(:get).with(path: "shop") # <-- fires using victim's offline access token

  get :index, params: { shop: "victim-shop.myshopify.com" } # no embedded param, no auth proof

  assert_response :ok
end
```

Expected (per the invariant) behavior would be a `401`/redirect *before* `ShopifyAPI::Clients::Rest::Admin.new`/`client.get` is ever invoked unless the caller can prove identity (e.g., valid ID token). The actual current behavior, confirmed by the pre-existing test `"returns :ok if the shop is installed"` [6](#0-5) , is that the API call fires unconditionally whenever `embedded` isn't `"1"`, confirming the bypass.

### Citations

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L18-26)
```ruby
      before_action :check_shop_domain

      if ShopifyApp.configuration.use_new_embedded_auth_strategy?
        include ShopifyApp::TokenExchange
        around_action :activate_shopify_session
      else
        before_action :check_shop_known
        before_action :validate_non_embedded_session
      end
```

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L29-59)
```ruby
    def current_shopify_domain
      if params[:shop].blank?
        ShopifyApp::Logger.info("Could not identify installed store from current_shopify_domain")
        return
      end

      @shopify_domain ||= ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
      ShopifyApp::Logger.info("Installed store:  #{@shopify_domain} - deduced from Shopify Admin params")
      @shopify_domain
    end

    def installed_shop_session
      @installed_shop_session ||= SessionRepository.retrieve_shop_session_by_shopify_domain(current_shopify_domain)
    end

    private

    def check_shop_domain
      redirect_to(ShopifyApp.configuration.login_url) unless current_shopify_domain
    end

    def check_shop_known
      @shop = installed_shop_session
      unless @shop
        if embedded_param?
          redirect_for_embedded
        else
          redirect_to(shop_login)
        end
      end
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

**File:** lib/shopify_app/controller_concerns/redirect_for_embedded.rb (L22-24)
```ruby
    def loaded_directly_from_admin?
      ShopifyApp.configuration.embedded_app? && params[:embedded] == "1"
    end
```

**File:** test/controllers/concerns/ensure_installed_test.rb (L54-67)
```ruby
  test "returns :ok if the shop is installed" do
    session = mock
    ShopifyApp::SessionRepository.stubs(:retrieve_shop_session_by_shopify_domain).returns(session)

    client = mock
    ShopifyAPI::Clients::Rest::Admin.expects(:new).with(session: session).returns(client)
    client.expects(:get)

    shopify_domain = "shop1.myshopify.com"

    get :index, params: { shop: shopify_domain }

    assert_response :ok
  end
```

**File:** test/controllers/concerns/ensure_installed_test.rb (L135-140)
```ruby
  test "does not perform a session validation check if coming from an embedded" do
    ShopifyApp::SessionRepository.stubs(:retrieve_shop_session_by_shopify_domain)
    ShopifyAPI::Clients::Rest::Admin.expects(:new).never

    get :index, params: { shop: "shop1.myshopify.com" }
  end
```
