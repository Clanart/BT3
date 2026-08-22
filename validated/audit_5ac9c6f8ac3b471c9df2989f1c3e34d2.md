### Title
`current_shopify_domain` prioritizes unauthenticated `params[:shop]` over the verified session shop, letting an attacker-controlled shop domain be injected into the CSP `frame-ancestors` directive - ([File: lib/shopify_app/controller_concerns/login_protection.rb])

### Summary
`current_shopify_domain` resolves as `requested_shopify_domain || authenticated_shopify_domain`, meaning an unauthenticated `params[:shop]` value always takes precedence over the shop bound to the verified session cookie/JWT. `FrameAncestors` consumes this value directly to build the `Content-Security-Policy: frame-ancestors` header, so the directive can reflect an attacker-chosen (but format-valid) shop domain instead of the actual authenticated shop.

### Finding Description
`requested_shopify_domain` is defined as `sanitized_shop_name`, which comes straight from `sanitize_shop_param(params)` → `ShopifyApp::Utils.sanitize_shop_domain(params[:shop])` [1](#0-0) [2](#0-1) . `sanitize_shop_domain` only validates that the value matches the `*.myshopify.com`-style domain format; it does not verify that the value corresponds to the caller's authenticated shop.

`current_shopify_domain` is implemented as:
```
shopify_domain = requested_shopify_domain || authenticated_shopify_domain
``` [3](#0-2) 

This means whenever `params[:shop]` is present and sanitizable, it wins over `authenticated_shopify_domain` (`current_shopify_session&.shop`, derived from the verified session) — even if a valid, authenticated session already exists for a different shop. `FrameAncestors` then uses this value verbatim: `domain_host = current_shopify_domain || "*.#{myshopify_domain}"`, embedding it into the `frame-ancestors` CSP directive [4](#0-3) .

No check anywhere in this call path (`sanitize_shop_param`, `current_shopify_domain`) compares `params[:shop]` against the session-bound shop before it's used for `frame-ancestors`. The only place such a comparison exists (`session_shop_conflicts_with_params` in `login_again_if_different_user_or_shop`) is a separate, opt-in check that a controller must explicitly invoke and is not called by `FrameAncestors` or by `current_shopify_domain` itself [5](#0-4) [6](#0-5) .

Exploit flow: an attacker crafts a link to any app route with `?shop=attacker-controlled-but-valid-myshopify-domain` (a real, attacker-owned `*.myshopify.com` dev/store domain passes `sanitize_shop_domain`). Whether or not the victim has an authenticated session, `current_shopify_domain` returns the attacker's domain instead of the session's real shop, and the resulting `Content-Security-Policy: frame-ancestors` header names the attacker's origin as a permitted framing origin for that response, rather than the merchant's real installed shop's admin origin.

### Impact Explanation
This weakens the CSP `frame-ancestors` protection that is supposed to restrict who may iframe the app's page to the specific authenticated merchant's admin (`https://{shop}.myshopify.com` / `https://admin.shopify.com`) [7](#0-6) . Because the directive is derived from an unauthenticated, attacker-supplied `shop` parameter rather than the verified session, an attacker can name their own origin as an allowed frame-ancestor for a victim's response, which is a precondition/enabler for clickjacking-style attacks (e.g. tricking a merchant into performing an authenticated action inside an attacker-controlled iframe). This maps to Shopify's "Improper platform/embedded app framing controls" / clickjacking impact class — it is a policy/defense-in-depth weakening rather than a direct token or data leak.

### Likelihood Explanation
Feasibility is moderate: the attacker needs only to get a victim (already authenticated in the app for their real shop, or unauthenticated) to load an app URL with an attacker-controlled `?shop=` value. Since `sanitize_shop_domain` accepts any syntactically valid `*.myshopify.com` domain — including domains the attacker legitimately owns (e.g., a free dev store) — this precondition is trivially satisfiable without any secret, token, or insider access. The header effect is fully reproducible and deterministic given the code path shown.

### Recommendation
In `current_shopify_domain`, prefer the verified `authenticated_shopify_domain` over the unauthenticated `requested_shopify_domain` whenever a valid session exists (i.e. flip the precedence to `authenticated_shopify_domain || requested_shopify_domain`, or only fall back to the request param when there is no session at all). At minimum, `FrameAncestors` should source `domain_host` only from `authenticated_shopify_domain`/`current_shopify_session&.shop`, never from unauthenticated `params[:shop]`.

### Proof of Concept
```ruby
# test/controllers/concerns/frame_ancestors_test.rb (illustrative)
test "frame-ancestors reflects unauthenticated params[:shop] instead of the real session shop" do
  # victim has an authenticated session for real-shop.myshopify.com
  sign_in_as("real-shop.myshopify.com")

  get "/some_protected_page", params: { shop: "attacker-shop.myshopify.com" }

  csp = response.headers["Content-Security-Policy"]
  assert_includes csp, "frame-ancestors https://attacker-shop.myshopify.com"
  refute_includes csp, "frame-ancestors https://real-shop.myshopify.com"
end
```
Expected (buggy) result: the CSP names `attacker-shop.myshopify.com`, not the session-bound `real-shop.myshopify.com`, confirming `current_shopify_domain` (and therefore `FrameAncestors`) trusts the unauthenticated `params[:shop]` over the verified session.

### Citations

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L70-76)
```ruby
    def login_again_if_different_user_or_shop
      return unless session_id_conflicts_with_params || session_shop_conflicts_with_params

      ShopifyApp::Logger.debug("Clearing session and redirecting to login")
      clear_shopify_session
      redirect_to_login
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L208-210)
```ruby
    def requested_shopify_domain
      sanitized_shop_name
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L212-220)
```ruby
    def authenticated_shopify_domain
      current_shopify_session&.shop
    end

    def current_shopify_domain
      shopify_domain = requested_shopify_domain || authenticated_shopify_domain
      ShopifyApp::Logger.info("Installed store  - #{shopify_domain} deduced from user session")
      shopify_domain
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L251-253)
```ruby
    def session_shop_conflicts_with_params
      current_shopify_session && params[:shop].is_a?(String) && current_shopify_session.shop != params[:shop]
    end
```

**File:** lib/shopify_app/controller_concerns/sanitized_params.rb (L7-26)
```ruby
    def sanitized_shop_name
      @sanitized_shop_name ||= sanitize_shop_param(params)
    end

    def referer_sanitized_shop_name
      return unless request.referer.present?

      @referer_sanitized_shop_name ||= begin
        referer_uri = URI(request.referer)
        query_params = Rack::Utils.parse_query(referer_uri.query)

        sanitize_shop_param(query_params.with_indifferent_access)
      end
    end

    def sanitize_shop_param(params)
      return unless params[:shop].present?

      ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
    end
```

**File:** lib/shopify_app/controller_concerns/frame_ancestors.rb (L8-16)
```ruby
      content_security_policy do |policy|
        policy.frame_ancestors(-> do
          domain_host = current_shopify_domain || "*.#{::ShopifyApp.configuration.myshopify_domain}"
          [
            "#{ShopifyAPI::Context.host_scheme}://#{domain_host}",
            "https://admin.#{::ShopifyApp.configuration.unified_admin_domain}",
          ]
        end)
      end
```
