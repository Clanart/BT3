### Title
Callback redirect accepts unencrypted (`http://`) `host`-derived URLs, enabling a downgraded post-OAuth redirect - (File: app/controllers/shopify_app/callback_controller.rb)

### Summary
`ShopifyApp::CallbackController#redirect_to_app` builds the post-authentication redirect target from the base64-encoded `host` request parameter, and the only safety check performed (`deduced_phishing_attack?`) validates *only the hostname* of that decoded URL, never its URL scheme. `ShopifyApp::Utils.sanitize_shop_domain`, which backs this check, explicitly and intentionally treats `http://` and `https://` shop URLs as equally valid, as confirmed by both the implementation and its test suite. As a result, a `host` parameter that decodes to an `http://`-scheme URL for an otherwise-trusted domain will pass the "phishing" check and the browser will be redirected to that unencrypted URL after a successful OAuth login.

### Finding Description
`deduced_phishing_attack?` computes `sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)` and only inspects `.host`, discarding the scheme entirely: [1](#0-0) 

`sanitize_shop_domain` itself normalizes any scheme-less input to `https://` for parsing, but happily accepts an explicit `http://` scheme supplied by the caller without complaint: [2](#0-1) 

This is exercised directly by the test suite, which lists `"http://my-shop.myshopify.com"` alongside `"https://my-shop.myshopify.com"` as an equally "good" (accepted) input to `sanitize_shop_domain`: [3](#0-2) 

Because `deduced_phishing_attack?` never rejects `http` schemes, `redirect_to_app` will use the attacker-influenced `decoded_host` (via `ShopifyAPI::Auth.embedded_app_url(params[:host])`) verbatim as the redirect target immediately after OAuth completes: [4](#0-3) 

The `host` parameter is fully attacker-controlled input on the OAuth callback route, which is unauthenticated at the HTTP layer (it is the entry point that establishes the session), so this path is reachable by an unrelated/anonymous request crafting a callback URL with a chosen `host` value.

### Impact Explanation
If the decoded `host` value resolves to a trusted domain (`myshopify.com`, `shopify.com`, etc.) but with an `http://` scheme, the app will not flag it as phishing and will issue a top-level redirect (`allow_other_host: true`) to that unencrypted URL right after OAuth login completes. Any sensitive data embedded in that redirect (return-to path, query parameters, and potentially the App Bridge/host context used to re-establish the embedded session) is sent over an unencrypted channel, allowing a network-position (MITM) attacker to intercept or rewrite the response, matching the class of issue described in the source report (acceptance of non-local unencrypted URL schemes with no rejection or warning).

### Likelihood Explanation
Medium. The attacker needs to control or influence the `host` param on the callback URL (e.g., via a crafted install/login link) and also be positioned on the network path of the victim to actually exploit the resulting unencrypted redirect. The validation gap itself is deterministic and always reachable — there is no rejection of `http://` anywhere in `sanitize_shop_domain` or `deduced_phishing_attack?` — but real-world exploitation additionally requires a MITM vantage point, consistent with the "Medium" difficulty rating in the original report.

### Recommendation
In `ShopifyApp::Utils.sanitize_shop_domain` (and specifically in `CallbackController#deduced_phishing_attack?`), reject or explicitly warn on non-`https` schemes for any externally supplied shop/host URL rather than validating hostname alone. At minimum, `deduced_phishing_attack?` should also check `URI(decoded_host).scheme == "https"` and treat any other scheme as a phishing/MITM risk, redirecting to `ShopifyApp.configuration.root_url` instead of trusting the value.

### Proof of Concept
1. Craft a base64-encoded `host` parameter whose decoded form is `http://<trusted-shop>.myshopify.com/admin` (i.e., a value that `ShopifyAPI::Auth.embedded_app_url` will turn into an `http://` URL for a hostname inside `TRUSTED_SHOPIFY_DOMAINS`).
2. Complete (or trigger) the OAuth callback flow at `/auth/shopify/callback?...&host=<crafted_value>`.
3. Observe that `deduced_phishing_attack?` returns `false` (no phishing detected) because `sanitize_shop_domain` only inspects `URI(decoded_host).host`, ignoring the `http` scheme, as shown by the accepted `"http://my-shop.myshopify.com"` test case.
4. `redirect_to_app` issues `redirect_to(decoded_host + return_to, allow_other_host: true)`, sending the victim's browser to the unencrypted `http://` URL post-login, exposing that traffic to interception/modification by a network attacker.

### Citations

**File:** app/controllers/shopify_app/callback_controller.rb (L80-94)
```ruby
    def redirect_to_app
      if ShopifyAPI::Context.embedded?
        return_to = session.delete(:return_to)
        redirect_to = if fully_formed_url?(return_to)
          return_to
        else
          "#{decoded_host}#{return_to}"
        end

        redirect_to = ShopifyApp.configuration.root_url if deduced_phishing_attack?
        redirect_to(redirect_to, allow_other_host: true)
      else
        redirect_to(return_address)
      end
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L101-113)
```ruby
    def decoded_host
      @decoded_host ||= ShopifyAPI::Auth.embedded_app_url(params[:host])
    end

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

**File:** test/shopify_app/utils_test.rb (L10-22)
```ruby
  [
    "my-shop",
    "my-shop.myshopify.com",
    "https://my-shop.myshopify.com",
    "http://my-shop.myshopify.com",
    "my-shop.shop.dev",
    "https://my-shop.shop.dev",
    "http://my-shop.shop.dev",
  ].each do |good_url|
    test "sanitize_shop_domain for (#{good_url})" do
      assert ShopifyApp::Utils.sanitize_shop_domain(good_url)
    end
  end
```
