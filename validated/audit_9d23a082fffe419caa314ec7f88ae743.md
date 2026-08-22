### Title
Cross-Shop Session Hijack via Unified Admin URL Path Smuggling in `sanitize_shop_domain` - (File: lib/shopify_app/utils.rb)

### Summary
`ShopifyApp::Utils.sanitize_shop_domain` is supposed to only accept shop identifiers that are genuinely on a trusted Shopify domain (either `{shop}.myshopify.com`-style subdomains or Shopify's unified admin URL `admin.shopify.com/store/{shop}`). The logic that implements the unified-admin case does not validate that the URL path is actually of the exact shape `/store/{shop}`; it blindly extracts the last path segment as the "shop name." This mirrors the reported bug class: the code's proposition ("this is a genuine unified-admin URL for shop X") does not match what the logic actually verifies (it only checks that the host starts with `admin` and the eTLD+1 is a trusted domain — not the full path shape).

### Finding Description
`sanitize_shop_domain` iterates over trusted domains and for each one computes: [1](#0-0) 

The unified-admin branch is gated only by `unified_admin?(uri) && from_trusted_domain`: [2](#0-1) 

and the shop name is derived with: [3](#0-2) 

`unified_admin?` only checks that the **first host label** is `"admin"`:
```ruby
def unified_admin?(uri)
  uri.host.split(".").first == "admin"
end
```
There is no check that the URL path is exactly `/store/{shop}`. `myshopify_domain_from_unified_admin` simply takes `uri.path.split("/").last`, i.e. whatever the *final* path segment is, regardless of how many segments precede it.

This means a URL such as:
```
https://admin.shopify.com/store/anything/apps/VICTIM-SHOP-NAME
```
will satisfy `unified_admin?(uri)` (host starts with `admin`) and `from_trusted_domain` (the eTLD+1 is `shopify.com`), causing the function to return `"VICTIM-SHOP-NAME.myshopify.com"` — a fully "sanitized," trusted-looking shop domain string that the attacker chose, not one that was actually verified to belong to the request.

The only test coverage for this path exercises `/store/store-name` and `/store/store-name/` — never a deeper/multi-segment path — so the missing validation is unguarded by tests: [4](#0-3) 

### Impact Explanation
This sanitizer is used directly on the anonymous, attacker-controlled `shop` request parameter in `ShopifyApp::EnsureInstalled`, which is the concern apps use to load the shop's session before doing anything else: [5](#0-4) 

When the app is **not** using the new Token Exchange strategy (still a fully supported, common configuration for classic OAuth apps), `EnsureInstalled` uses `check_shop_known`, which loads `installed_shop_session` purely from `sanitize_shop_domain(params[:shop])` — with no cryptographic (JWT/session-token) cross-check: [6](#0-5) [7](#0-6) 

If an attacker sends `shop=https%3A%2F%2Fadmin.shopify.com%2Fstore%2Fx%2Fapps%2Fvictim-shop` to an endpoint protected by `EnsureInstalled` (classic OAuth mode), `sanitize_shop_domain` returns `"victim-shop.myshopify.com"` and `installed_shop_session` will load the **victim shop's stored session** (offline access token) from `SessionRepository`. `check_shop_known` then treats the request as already-authenticated for the victim shop and proceeds to `validate_non_embedded_session`, which performs an authenticated Admin API call **using the victim's access token** on behalf of the attacker's request. Depending on how the app then renders data or acts using `@shop`/`installed_shop_session`, this is a cross-shop session/data access primitive driven entirely by an anonymous request — the attacker never needs a valid session token, cookie, or OAuth flow of their own; they only need to know (or guess) the victim's `*.myshopify.com` name, which is often not secret.

Note: this specific bypass does **not** apply to the newer Token Exchange strategy, because `TokenExchange#reject_mismatched_requested_shopify_domain` independently cross-checks the requested shop against the cryptographically verified `jwt_shopify_domain` from the session token: [8](#0-7) 
So the exploitable surface is specifically apps using `EnsureInstalled` with the classic (non-Token-Exchange) OAuth flow.

### Likelihood Explanation
`EnsureInstalled` with the classic OAuth path (`use_new_embedded_auth_strategy? == false`) is still generator-produced, documented behavior and a normal supported configuration, not a deprecated/removed one. The `shop` parameter is a standard, unauthenticated, attacker-suppliable query parameter in every route that uses this concern. Crafting the payload requires no secrets — only a known/guessed target `myshopify.com` subdomain name, which is typically public (shown in storefront URLs, app listing pages, partner directories, etc.). The only uncertainty is the exact runtime behavior of `Addressable::URI#domain`/`#host` parsing for a URL like `https://admin.shopify.com/store/x/apps/victim-shop` (i.e., whether `uri.domain` resolves to exactly `"shopify.com"` and `uri.path` to `/store/x/apps/victim-shop`), which I could not execute directly, but based on the existing passing tests for `/store/store-name` and `/store/store-name/` shapes, the parsing behaves as a standard path split, and nothing in `myshopify_domain_from_unified_admin` restricts path depth.

### Recommendation
In `myshopify_domain_from_unified_admin`, validate that `uri.path` matches exactly `/store/{shop}` (e.g., with a strict regex `%r{\A/store/([^/]+)/?\z}`) before extracting the shop segment, and return `nil`/reject otherwise. Additionally, consider requiring the same explicit path-shape check inside `unified_admin?` itself so the "is this a genuine unified admin URL" predicate actually reflects the real Shopify unified-admin URL structure (`admin.shopify.com/store/{shop}[...]`) rather than only checking the leading host label.

### Proof of Concept
1. Have (or guess) a victim's installed shop domain, e.g. `victim-shop.myshopify.com`, for an app configured with `EnsureInstalled` and classic OAuth (`use_new_embedded_auth_strategy? == false`).
2. As an unauthenticated attacker, send a request to any app route protected by `EnsureInstalled`, e.g.:
   ```
   GET /some_protected_path?shop=https%3A%2F%2Fadmin.shopify.com%2Fstore%2Fanything%2Fapps%2Fvictim-shop
   ```
3. `current_shopify_domain` calls `ShopifyApp::Utils.sanitize_shop_domain("https://admin.shopify.com/store/anything/apps/victim-shop")`.
4. Inside `sanitize_shop_domain`, for `trusted_domain = "shopify.com"`: `unified_admin?(uri)` is `true` (host `admin.shopify.com` starts with `admin`), `from_trusted_domain` is `true` (eTLD+1 is `shopify.com`), so it returns `myshopify_domain_from_unified_admin(uri)`, which computes `uri.path.split("/").last` → `"victim-shop"` → returns `"victim-shop.myshopify.com"`.
5. `installed_shop_session` then looks up and loads the victim shop's stored `SessionRepository` session using this attacker-supplied, spoofed domain, and `check_shop_known` allows the request to proceed as if authenticated for `victim-shop.myshopify.com`, with subsequent Admin API calls in `validate_non_embedded_session` executed using the victim's access token.

### Citations

**File:** lib/shopify_app/utils.rb (L14-27)
```ruby
      def sanitize_shop_domain(shop_domain)
        uri = uri_from_shop_domain(shop_domain)
        return if uri.nil? || uri.host.nil?

        trusted_domains.each do |trusted_domain|
          no_shop_name_in_subdomain = uri.host == trusted_domain
          from_trusted_domain = trusted_domain == uri.domain

          return myshopify_domain_from_unified_admin(uri) if unified_admin?(uri) && from_trusted_domain
          return nil if no_shop_name_in_subdomain || uri.host&.empty?
          return uri.host if from_trusted_domain
        end
        nil
      end
```

**File:** lib/shopify_app/utils.rb (L83-85)
```ruby
      def unified_admin?(uri)
        uri.host.split(".").first == "admin"
      end
```

**File:** lib/shopify_app/utils.rb (L87-91)
```ruby
      def myshopify_domain_from_unified_admin(uri)
        shop = uri.path.split("/").last

        "#{shop}.myshopify.com"
      end
```

**File:** test/shopify_app/utils_test.rb (L56-64)
```ruby
  test "convert unified admin to old domain" do
    trailing_forward_slash_url = "https://admin.shopify.com/store/store-name/"
    unified_admin_url = "https://admin.shopify.com/store/store-name"

    expected = "store-name.myshopify.com"

    assert_equal expected, ShopifyApp::Utils.sanitize_shop_domain(trailing_forward_slash_url)
    assert_equal expected, ShopifyApp::Utils.sanitize_shop_domain(unified_admin_url)
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

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L50-59)
```ruby
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

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L73-83)
```ruby
    def reject_mismatched_requested_shopify_domain
      requested_domain = requested_shopify_domain
      return false if requested_domain.blank?

      authenticated_domain = authenticated_shopify_domain_from_token
      return false if authenticated_domain.blank? || authenticated_domain == requested_domain

      ShopifyApp::Logger.debug("Shop context validation failed")
      head(:unauthorized)
      true
    end
```
