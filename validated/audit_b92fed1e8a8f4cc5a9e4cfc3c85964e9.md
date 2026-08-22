### Title
Cross-shop session/token selection via unauthenticated `shop` parameter in `EnsureInstalled` - ([File: app/controllers/concerns/shopify_app/ensure_installed.rb])

### Summary
`ShopifyApp::EnsureInstalled#current_shopify_domain` derives the tenant identity directly from the unauthenticated `params[:shop]` request parameter and this value is used to select and activate the stored offline access-token session for that shop, rather than deriving identity from a verified token (JWT/session cookie). This is the same bug class as the reported "stale/incorrect identifier used for routing" issue: a value that should only be used for bootstrap/UI purposes is instead used to choose which credential (access token) is loaded and used to serve the request.

### Finding Description
In the legacy (non-embedded-auth-strategy) flow, `EnsureInstalled` computes the shop context purely from the request parameter: [1](#0-0) 

```ruby
def current_shopify_domain
  if params[:shop].blank?
    ...
    return
  end
  @shopify_domain ||= ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
  ...
  @shopify_domain
end

def installed_shop_session
  @installed_shop_session ||= SessionRepository.retrieve_shop_session_by_shopify_domain(current_shopify_domain)
end
```

There is no JWT/session-token verification anywhere in this concern — `current_shopify_domain` is only sanitized, not authenticated. Its only guard, `check_shop_known`, merely checks that *some* session exists for that shop domain and, for non-embedded requests, `validate_non_embedded_session` performs an Admin API call using the *loaded* session as a liveness check, not as an authorization check: [2](#0-1) 

This is exactly the pattern the codebase's own documentation warns against for the `TokenExchange` concern: `requested_shopify_domain`/param-derived shop values should be used "for bootstrap or routing use cases only; do not use it for authorization, tenant lookup, or choosing a stored access token." [3](#0-2) 

`EnsureInstalled` violates this exact guidance: it uses the raw `params[:shop]` value both for tenant lookup (`installed_shop_session`) and for choosing which stored access token session to activate for the remainder of the request — analogous to the audit finding, where a stale/wrong round value was used instead of the actual authenticated one, causing state (votes/credentials) to be routed to the wrong destination.

### Impact Explanation
Any anonymous or unrelated party can send a GET/POST request with `shop=<any-installed-shop>.myshopify.com`. If that shop has a stored offline session (any previously installed shop), `installed_shop_session` loads and activates that shop's stored access token, and the controller action executes using that store's credentials/data rather than the identity of the actual caller. This is a cross-shop access/session-selection issue reachable without any signed session token, ID token, or HMAC verification — matching the "cross-shop access" and "session storage lookup" categories called out as acceptable analogs.

### Likelihood Explanation
`EnsureInstalled` is a first-class, documented, shipped concern still used by apps that have not migrated to the "new embedded auth strategy" / Shopify managed installation (`unless ShopifyApp.configuration.use_new_embedded_auth_strategy?`), so it is reachable in production for any app using the legacy flow. The trigger requires only an unauthenticated HTTP request with a chosen `shop` parameter — no secrets, developer cooperation, or special host configuration needed.

### Recommendation
Do not use `params[:shop]` to select or activate a stored session/access token. Derive `current_shopify_domain` from a verified source (session cookie-backed `ShopifyAPI::Auth::Session` or a validated ID token/JWT), consistent with the guidance already documented for `TokenExchange`, and treat any `shop` request parameter strictly as a hint for redirect/login URLs, never for `SessionRepository` lookups or session activation.

### Proof of Concept
1. Shop A (attacker, or any anonymous requester with no valid Shopify session) sends: `GET /some_ensure_installed_protected_path?shop=shop-b.myshopify.com`
2. `check_shop_domain` passes because `params[:shop]` is present and sanitizes to a valid domain. [4](#0-3) 
3. `check_shop_known` calls `installed_shop_session`, which retrieves Shop B's stored offline session via `SessionRepository.retrieve_shop_session_by_shopify_domain("shop-b.myshopify.com")` — succeeding because Shop B is a legitimately installed, unrelated merchant. [5](#0-4) 
4. `validate_non_embedded_session` uses this loaded session (`installed_shop_session`) to call the Shopify Admin API — succeeding as long as Shop B's token is valid, with no cross-check that the caller is actually Shop B. [6](#0-5) 
5. The action proceeds with `@shop` bound to Shop B's session/access token, letting the requester perform actions against Shop B's store data using Shop B's credentials, without ever proving they are Shop B.

### Citations

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L29-42)
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
```

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L46-82)
```ruby
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

    def shop_login
      url = URI(ShopifyApp.configuration.login_url)

      url.query = URI.encode_www_form(
        shop: params[:shop],
        host: params[:host],
        return_to: request.fullpath,
      )

      url.to_s
    end

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

**File:** docs/shopify_app/sessions.md (L242-242)
```markdown
In token exchange authenticated controllers using `EnsureHasSession`, `current_shopify_domain` and `authenticated_shopify_domain` resolve to the shop from the verified ID token/session. Embedded document requests can arrive without a usable token, for example after server-side redirects; the concern uses the configured invalid-token response path to get a fresh token before authenticated action code continues. Request shop context is validated against the authenticated context before the action runs. `requested_shopify_domain` resolves the sanitized `shop` query parameter for bootstrap or routing use cases only; do not use it for authorization, tenant lookup, or choosing a stored access token.
```
