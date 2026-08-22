No vulnerability confirmed for this question.

**Analysis:**

The `TRUSTED_SHOPIFY_DOMAINS` list in `lib/shopify_app/utils.rb` already explicitly includes `myshopify.io` and `spin.dev` by design [1](#0-0) . These are not attacker-introduced widenings — they are intentional entries covering Shopify's regional (`.io`) and internal spin/staging (`spin.dev`) environments, confirmed by test cases that assert these domains are accepted as valid (`my-shop.myshopify.io`, `shopify.foobar-....spin.dev`) [2](#0-1) .

Matching against these domains is done via `uri.domain` (Addressable's public-suffix-aware eTLD+1 computation), not naive suffix/substring matching, so an attacker cannot spoof with a crafted subdomain like `foo.myshopify.io.evil.ru` — that resolves to `evil.ru` as the eTLD+1, not `myshopify.io`, and is correctly rejected (verified by the existing bad-URL test cases) [3](#0-2) [4](#0-3) .

Every reachable caller of `sanitize_shop_domain`/`current_shopify_domain` treats a `nil` result as fail-closed with no default/wildcard fallback:
- `EnsureInstalled#check_shop_domain` redirects to `login_url` when `current_shopify_domain` is nil [5](#0-4) .
- `SessionsController#validate_shop_presence` renders an invalid-shop error and returns `false` if `sanitized_shop_name` is nil [6](#0-5) .
- `CallbackController#deduced_phishing_attack?` and `EmbeddedApp#deduced_phishing_attack?` treat a nil sanitized host as a phishing attempt and reject/redirect to root [7](#0-6) [8](#0-7) .
- `ShopAccessScopesVerification#current_shopify_domain` returns nil on blank param, feeding into scope-mismatch check without a default fallback [9](#0-8) .

None of these code paths fall through to a default or wildcard shop on `nil`; they all reject/redirect. The premise that `shop.myshopify.io`/`shop.spin.dev` "widen the accepted set" beyond intent does not hold, since these are deliberately trusted domains handled with public-suffix-aware comparison, and the fail-closed invariant is upheld across all identified call sites.

### Citations

**File:** lib/shopify_app/utils.rb (L6-12)
```ruby
      TRUSTED_SHOPIFY_DOMAINS = [
        "shopify.com",
        "myshopify.io",
        "myshopify.com",
        "spin.dev",
        "shop.dev",
      ].freeze
```

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

**File:** test/shopify_app/utils_test.rb (L24-43)
```ruby
  [
    "my-shop",
    "my-shop.myshopify.io",
    "http-shop-from-qa-hell.myshopify.com",
    "https://my-shop.myshopify.io",
    "http://my-shop.myshopify.io",
  ].each do |good_url|
    test "sanitize_shop_domain URL (#{good_url}) with custom myshopify_domain" do
      ShopifyApp.configuration.myshopify_domain = "myshopify.io"
      assert ShopifyApp::Utils.sanitize_shop_domain(good_url)
    end
  end

  test "sanitize_shop_domain URL shopify spin.dev custom myshopify_domain" do
    myshop_domain = "http://shopify.foobar-part-onboard-0d6x.asdf-rygus.us.spin.dev"
    ShopifyApp.configuration.stubs(:myshopify_domain).returns(myshop_domain)
    unified_admin_url = myshop_domain + "/store/shop1/apps/cool_app_hansel"

    assert ShopifyApp::Utils.sanitize_shop_domain(unified_admin_url)
  end
```

**File:** test/shopify_app/utils_test.rb (L66-79)
```ruby
  [
    "myshop.com",
    "myshopify.com",
    "shopify.com",
    "two words",
    "store.myshopify.com.evil.com",
    "/foo/bar",
    "foo.myshopify.io.evil.ru",
    "javascript:alert(1)",
  ].each do |bad_url|
    test "sanitize_shop_domain for a non-myshopify URL (#{bad_url})" do
      assert_nil ShopifyApp::Utils.sanitize_shop_domain(bad_url)
    end
  end
```

**File:** app/controllers/concerns/shopify_app/ensure_installed.rb (L46-48)
```ruby
    def check_shop_domain
      redirect_to(ShopifyApp.configuration.login_url) unless current_shopify_domain
    end
```

**File:** app/controllers/shopify_app/sessions_controller.rb (L99-107)
```ruby
    def validate_shop_presence
      @shop = sanitized_shop_name
      unless @shop
        render_invalid_shop_error
        return false
      end

      true
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L105-113)
```ruby
    # host param doesn't match the configured myshopify_domain
    def deduced_phishing_attack?
      sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)
      if sanitized_host.nil?
        ShopifyApp::Logger.info("host param from callback is not from a trusted domain")
        ShopifyApp::Logger.info("redirecting to root as this is likely a phishing attack")
      end
      sanitized_host.nil?
    end
```

**File:** lib/shopify_app/controller_concerns/embedded_app.rb (L59-67)
```ruby
    def deduced_phishing_attack?(decoded_host)
      sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(decoded_host) unless unsafe_embedded_host?(decoded_host)
      if sanitized_host.nil?
        message = "Host param for redirect to embed app in admin is not from a trusted domain, " \
          "redirecting to root as this is likely a phishing attack."
        ShopifyApp::Logger.info(message)
      end
      sanitized_host.nil?
    end
```

**File:** app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb (L34-38)
```ruby
    def current_shopify_domain
      return if params[:shop].blank?

      ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
    end
```
