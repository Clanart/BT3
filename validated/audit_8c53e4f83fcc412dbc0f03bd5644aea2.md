### Title
Unsanitized `params[:shop]` comparison in `session_shop_conflicts_with_params` allows logout-CSRF via shop-string variants that normalize to the current session's shop - (File: lib/shopify_app/controller_concerns/login_protection.rb)

### Summary
`session_shop_conflicts_with_params` compares `current_shopify_session.shop` (a normalized `*.myshopify.com` domain) against the raw, unsanitized `params[:shop]` string instead of the sanitized value produced by `sanitize_shop_param`/`sanitized_shop_name`. An attacker can craft a `shop` value that is textually different but semantically the same shop, causing a false "conflict" that triggers `clear_shopify_session` and `redirect_to_login`, logging the victim out via a plain GET link with no CSRF token.

### Finding Description
`activate_shopify_session` calls `login_again_if_different_user_or_shop`, which short-circuits to `session_id_conflicts_with_params || session_shop_conflicts_with_params`: [1](#0-0) 

`session_shop_conflicts_with_params` performs the comparison directly against `params[:shop]`, bypassing the app's own normalization logic: [2](#0-1) 

Elsewhere in the same module, the codebase explicitly normalizes shop params via `sanitize_shop_param`, which calls `ShopifyApp::Utils.sanitize_shop_domain`: [3](#0-2) 

`sanitize_shop_domain` downcases/strips the input, adds a scheme if missing, and parses it as a URI to extract `uri.host`, meaning `"MY-SHOP.myshopify.com"`, `"https://my-shop.myshopify.com"`, and `"my-shop.myshopify.com"` all normalize to the identical string `"my-shop.myshopify.com"`: [4](#0-3) [5](#0-4) 

Because `session_shop_conflicts_with_params` uses the raw, non-normalized `params[:shop]` in the `!=` comparison rather than `sanitized_shop_name`, any of these textually-different-but-semantically-equal variants will not equal `current_shopify_session.shop` (e.g. `"my-shop.myshopify.com" != "MY-SHOP.myshopify.com"`), triggering a false positive "different shop" conflict.

Exploit flow: an unprivileged attacker crafts a link/auto-submitting GET form to any controller action protected by `ShopifyApp::LoginProtection` with `activate_shopify_session` and `login_again_if_different_user_or_shop`, e.g. `GET /some_protected_path?shop=MY-SHOP.myshopify.com`. When the victim (who has an active session for `my-shop.myshopify.com`) loads the link, no CSRF token is required for GET requests, `current_shopify_session.shop` (`"my-shop.myshopify.com"`) will differ from raw `params[:shop]` (`"MY-SHOP.myshopify.com"`), the conflict check returns true, `clear_shopify_session` wipes the session cookie, and `redirect_to_login` redirects the victim to the login/OAuth flow — effectively a forced logout (logout-CSRF) purely from a GET request with no state-changing token required.

### Impact Explanation
This is a session-invalidation / logout-CSRF: an attacker can silently force any victim merchant user's active embedded-app session to be torn down and redirected into the OAuth login flow, without requiring any secret, valid access token, or victim interaction beyond clicking a link/loading an auto-submit form. While it doesn't directly steal data or tokens, it is a genuine forced-session-termination / denial-of-service against session state and matches Shopify's "session or access token issue" / CSRF-adjacent impact class (unauthorized session-state modification triggered by a low-privilege, unauthenticated request).

### Likelihood Explanation
High feasibility: the only precondition is that the victim has an active `LoginProtection`-based session and visits a URL containing a crafted `shop` param (case-different or scheme-prefixed) matching their own shop. No token, secret, or app-specific knowledge is required; the attacker only needs to know/guess the victim's shop domain, which is often public/discoverable. It is fully repeatable per victim/session, and works on any GET-accessible controller action using `activate_shopify_session` + `login_again_if_different_user_or_shop`.

### Recommendation
Change `session_shop_conflicts_with_params` to compare against the sanitized value instead of raw `params[:shop]`:

```ruby
def session_shop_conflicts_with_params
  current_shopify_session && sanitized_shop_name.present? && current_shopify_session.shop != sanitized_shop_name
end
```

This ensures the conflict check uses the same normalized shop identity (`ShopifyApp::Utils.sanitize_shop_domain`) used everywhere else in the module (e.g., `requested_shopify_domain`), so textually-different-but-equivalent shop strings no longer trigger an unwanted session clear.

### Proof of Concept
Using the existing `LoginProtectionController` test harness in `test/shopify_app/controller_concerns/login_protection_test.rb`:

```ruby
test "#login_again_if_different_user_or_shop should NOT clear session when shop param normalizes to same shop" do
  with_application_test_routes do
    cookies.encrypted[ShopifyAPI::Auth::Oauth::SessionCookie::SESSION_COOKIE_NAME] = "old-cookie"

    ShopifyAPI::Utils::SessionUtils.expects(:current_session_id).returns(
      ShopifyAPI::Auth::Session.new(shop: "my-shop.myshopify.com"),
    ).once

    # Attacker-crafted link: case-different shop that normalizes to the same shop
    get :second_login, params: { shop: "MY-SHOP.myshopify.com" }

    assert_response :ok
    assert_equal "old-cookie", cookies.encrypted[ShopifyAPI::Auth::Oauth::SessionCookie::SESSION_COOKIE_NAME]
  end
end
```

Expected (buggy) behavior: the test fails — the response redirects to `/login` and the session cookie is cleared, even though `"MY-SHOP.myshopify.com"` and `"my-shop.myshopify.com"` normalize to the identical shop via `ShopifyApp::Utils.sanitize_shop_domain`, confirming the logout-CSRF condition described.

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

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L251-253)
```ruby
    def session_shop_conflicts_with_params
      current_shopify_session && params[:shop].is_a?(String) && current_shopify_session.shop != params[:shop]
    end
```

**File:** lib/shopify_app/controller_concerns/sanitized_params.rb (L22-26)
```ruby
    def sanitize_shop_param(params)
      return unless params[:shop].present?

      ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
    end
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

**File:** lib/shopify_app/utils.rb (L68-81)
```ruby
      def uri_from_shop_domain(shop_domain)
        name = shop_domain.to_s.downcase.strip
        name += ".#{myshopify_domain}" if !name.include?(myshopify_domain.to_s) && !name.include?(".")
        uri = Addressable::URI.parse(name)

        if uri.scheme.nil?
          name = "https://" + name
          uri = Addressable::URI.parse(name)
        end

        uri
      rescue Addressable::URI::InvalidURIError
        nil
      end
```
