### Title
Open Redirect via userinfo (`@`) injection when concatenating `decoded_host` and `return_to` in `CallbackController#redirect_to_app` - ([File: app/controllers/shopify_app/callback_controller.rb])

### Summary
`CallbackController#redirect_to_app` builds the post-auth redirect target as `"#{decoded_host}#{return_to}"`, and validates only `decoded_host`'s own authority via `deduced_phishing_attack?`, never the fully-assembled string. `return_to` reaches this point after `SessionsController#copy_return_to_param_to_session` passes it through `RedirectSafely.make_safe`, which only rejects values that themselves carry a scheme/host — it does not reject a leading `@` character. Concatenated immediately after a trusted `decoded_host` value with no separating boundary enforcement, an `@`-prefixed `return_to` turns the trusted host into URL "userinfo" and shifts browser-perceived authority to an attacker-controlled domain.

### Finding Description
- `SessionsController#copy_return_to_param_to_session` stores `session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/")` [1](#0-0) . `RedirectSafely.make_safe` only rejects strings that parse to a URI with a scheme/host; a string like `@evil.com/x` has no scheme and no authority marker (`//`), so it is treated as a bare relative reference and passed through unchanged — it is not forced to start with `/`.
- In `CallbackController#redirect_to_app`, when the return_to is not already a "fully formed URL" (checked only for `scheme` + `host` via `Addressable::URI`), the code builds `"#{decoded_host}#{return_to}"` [2](#0-1) .
- `decoded_host` comes from `ShopifyAPI::Auth.embedded_app_url(params[:host])` [3](#0-2) , and is checked for trust only in isolation: `deduced_phishing_attack?` parses `URI(decoded_host).host` and runs it through `ShopifyApp::Utils.sanitize_shop_domain` [4](#0-3) . This check never re-examines the final concatenated string that is actually passed to `redirect_to(..., allow_other_host: true)`.
- If `return_to` is `@evil.com/x`, the resulting string is e.g. `https://admin.shopify.com/store/shop-name@evil.com/x`. Per the generic URI authority grammar, everything between the scheme/authority start and the last `@` before the next `/` is interpreted as "userinfo", making `evil.com` the actual host that browsers navigate to — while each component (`decoded_host` alone, and `return_to` alone under `RedirectSafely`) individually looked "safe".
- Notably, the sibling module `EmbeddedApp#unsafe_embedded_host?` in this same codebase explicitly guards against exactly this `@`-injection pattern for the `host` param (`embedded_host_authority(decoded_host).include?("@")`) [5](#0-4) , showing the maintainers are aware of this attack class — but no equivalent guard exists for `return_to` or for the final concatenated string in `CallbackController#redirect_to_app`.

### Impact Explanation
This is an Open Redirect at the end of the OAuth callback flow: after Shopify completes the OAuth handshake and the app sets its session/auth cookies, the merchant's browser is redirected (`allow_other_host: true`) to an attacker-chosen origin disguised behind a seemingly-trusted Shopify host prefix. This matches Shopify's Open Redirect impact class and can be leveraged for phishing (an attacker page masquerading as post-install Shopify admin) immediately following a real OAuth grant, increasing victim trust in the phishing page. Session cookies themselves are not directly exfiltrated to the attacker domain (they remain scoped to the app's own domain), so the impact is bounded to redirect/phishing rather than direct token theft.

### Likelihood Explanation
The attacker needs to control both the `return_to` param on the initial `/login` request and the `host` param on the callback for the *same* OAuth flow, which an unprivileged/merchant-controlled request can supply — both are attacker-influenced query params in a normal, unauthenticated install/login flow. Exploitability further depends on the exact string format `ShopifyAPI::Auth.embedded_app_url` returns for `decoded_host` (not present in this repo, defined in the `shopify_api` gem) having no delimiter that would neutralize a leading `@` in `return_to`, so full confirmation requires an integration test against a real `decoded_host` value.

### Recommendation
Validate the fully assembled redirect target's authority (not just `decoded_host` in isolation) before redirecting in `redirect_to_app`, e.g. by parsing `"#{decoded_host}#{return_to}"` with `Addressable::URI`/`URI` and re-running `ShopifyApp::Utils.sanitize_shop_domain` against the resulting `.host`, and reject any `return_to` containing `@` or other authority-delimiter characters before concatenation — mirroring the existing `unsafe_embedded_host?` guard in `EmbeddedApp`.

### Proof of Concept
```ruby
# integration test sketch
get "/login", params: { shop: "shop-name.myshopify.com", return_to: "@evil.com/x" }
# session[:return_to] becomes "@evil.com/x" (RedirectSafely treats it as host-less/safe)
...
get "/auth/shopify/callback", params: { host: Base64.strict_encode64("shop-name.myshopify.com/admin"), shop: "shop-name.myshopify.com", code: "...", hmac: "...", timestamp: "...", state: "..." }
# Expect: response.headers["Location"] host resolves to "admin.shopify.com" / trusted myshopify domain
# Actual (if unpatched, depending on ShopifyAPI::Auth.embedded_app_url format):
# Location: "https://admin.shopify.com/store/shop-name@evil.com/x"
# -> parsed authority is "evil.com", not the trusted Shopify host
```

### Citations

**File:** app/controllers/shopify_app/sessions_controller.rb (L109-111)
```ruby
    def copy_return_to_param_to_session
      session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
    end
```

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

**File:** app/controllers/shopify_app/callback_controller.rb (L101-103)
```ruby
    def decoded_host
      @decoded_host ||= ShopifyAPI::Auth.embedded_app_url(params[:host])
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

**File:** lib/shopify_app/controller_concerns/embedded_app.rb (L69-85)
```ruby
    def unsafe_embedded_host?(decoded_host)
      return true if decoded_host.empty? || !decoded_host.valid_encoding?
      return true if unsafe_embedded_host_characters?(decoded_host)

      embedded_host_authority(decoded_host).include?("@")
    end

    def unsafe_embedded_host_characters?(decoded_host)
      decoded_host.each_char.any? do |character|
        character_code = character.ord
        character_code <= 0x20 || character_code == 0x7f || character == "\\"
      end
    end

    def embedded_host_authority(decoded_host)
      decoded_host.sub(%r{\Ahttps?://}i, "").split(%r{[/?#]}, 2).first.to_s
    end
```
