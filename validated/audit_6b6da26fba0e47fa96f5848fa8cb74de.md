### Title
Open redirect in OAuth callback due to inconsistent host-parsing when validating the `host` param - (File: app/controllers/shopify_app/callback_controller.rb)

### Summary
The Stargate bug is a "wrong destination address" class of vulnerability: a value trusted for one purpose (validation) does not match the value actually used for the sensitive action (sending funds / redirecting). In `ShopifyApp::CallbackController#redirect_to_app`, the phishing check (`deduced_phishing_attack?`) parses the attacker-influenced `host` param with Ruby's stdlib `URI` class, while the app's other host-validation path (`ShopifyApp::EmbeddedApp#safe_embedded_app_url`) uses a stricter, purpose-built validator (`unsafe_embedded_host?`) that explicitly rejects control characters, backslashes, and `@` characters, specifically because of "parser-differential" redirect bypasses (per `CHANGELOG.md` entry for #2078: "Harden embedded app host validation to prevent parser-differential open redirects"). The callback controller's own check was not updated with that same hardening.

### Finding Description
`redirect_to_app` builds the final redirect target from `decoded_host` (derived directly from `params[:host]` via `ShopifyAPI::Auth.embedded_app_url`) concatenated with the session's `return_to` value, and only guards against phishing via: [1](#0-0) 

`deduced_phishing_attack?` extracts the host using Ruby's stdlib `URI(decoded_host).host` and passes it to `ShopifyApp::Utils.sanitize_shop_domain`, which internally re-parses using `Addressable::URI`: [2](#0-1) [3](#0-2) 

This is a different (and weaker) validation path than the one used elsewhere in the gem for the same class of untrusted `host` input — `ShopifyApp::EmbeddedApp#safe_embedded_app_url`, which decodes the host and explicitly filters out unsafe characters (control chars, `\`, `@`) before trusting it: [4](#0-3) 

The changelog confirms this exact bug class was patched for the `EmbeddedApp` concern and the generated `HomeController` template, but the same fix was not visibly applied to `CallbackController#deduced_phishing_attack?`: [5](#0-4) 

Because two different URL parsers (Ruby's stdlib `URI` vs. `Addressable::URI`) are used across the trust-decision (`URI(decoded_host).host`) versus the ultimate value used to build the redirect string (`"#{decoded_host}#{return_to}"`, later handed to a browser which uses yet a third parser, WHATWG URL), any disagreement between these parsers on what constitutes the "host" component of a malformed/crafted string could let a validated-looking `decoded_host` actually resolve to an attacker-controlled origin in the browser, mirroring exactly the Stargate bug where the value checked/trusted differs from the value actually acted upon.

### Impact Explanation
If exploitable, this would let an attacker construct a `host` parameter that passes `deduced_phishing_attack?`'s trust check (appearing to belong to a trusted Shopify domain) while the actual browser-resolved redirect target is an attacker-controlled domain. Since `redirect_to(redirect_to, allow_other_host: true)` is called and the session cookie/id-token flow has just completed, this is a classic post-OAuth open redirect that could be leveraged for phishing (e.g. redirecting the merchant to a fake login/consent page immediately after a legitimate Shopify OAuth approval), which is a recognized high-impact concern for embedded-app redirect flows per the project's own security posture (see the multiple prior "phishing" fixes in `CHANGELOG.md`, e.g. #1605, #1608).

### Likelihood Explanation
I could **not concretely construct or confirm a parser-differential bypass string** within the scope of this investigation — I do not have access to the actual implementation of `ShopifyAPI::Auth.embedded_app_url` (it lives in the separate `shopify-api-ruby` gem, not in this repository), so I cannot fully trace what `decoded_host` looks like for a crafted `host` param, nor definitively prove that `URI(decoded_host).host` and the eventual browser-side host resolution diverge for some payload. The `deduced_phishing_attack?` code path is exercised by the test `"#callback returns to root if the host in the param doesn't match configuration indicating a potential phishing attack"`, which only tests a straightforward non-Shopify domain (`hackerman-evil-site.com`), not adversarial parser-differential payloads: [6](#0-5) 

### Recommendation
Apply the same hardened host-validation approach used in `ShopifyApp::EmbeddedApp#safe_embedded_app_url` / `unsafe_embedded_host?` to `CallbackController#deduced_phishing_attack?`, ensuring a single, consistent, strict parser (or explicit character allow-listing) is used both for the trust decision and for constructing the final redirect string, rather than relying on Ruby's stdlib `URI` for validation while the raw string is used downstream.

### Proof of Concept
Not able to produce a concrete working PoC payload without access to `ShopifyAPI::Auth.embedded_app_url`'s implementation (external gem, not in this repository) to determine the exact string shape of `decoded_host` for adversarial `host` values, and without being able to execute/test the code. This should be treated as an **unconfirmed analog** requiring further hands-on testing (e.g., via a Devin session with code execution) before being considered a proven vulnerability rather than a plausible bug-class match.

### Citations

**File:** app/controllers/shopify_app/callback_controller.rb (L80-113)
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

    def fully_formed_url?(return_to)
      uri = Addressable::URI.parse(return_to)
      uri.present? && uri.scheme.present? && uri.host.present?
    end

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

**File:** lib/shopify_app/controller_concerns/embedded_app.rb (L52-85)
```ruby
    def safe_embedded_app_url(host)
      decoded_host = Base64.decode64(host.to_s)
      return if deduced_phishing_attack?(decoded_host)

      ShopifyAPI::Auth.embedded_app_url(Base64.strict_encode64(decoded_host))
    end

    def deduced_phishing_attack?(decoded_host)
      sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(decoded_host) unless unsafe_embedded_host?(decoded_host)
      if sanitized_host.nil?
        message = "Host param for redirect to embed app in admin is not from a trusted domain, " \
          "redirecting to root as this is likely a phishing attack."
        ShopifyApp::Logger.info(message)
      end
      sanitized_host.nil?
    end

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

**File:** CHANGELOG.md (L4-11)
```markdown
23.0.3 (June 24, 2026)
----------
- Token-exchange requests whose `shop` query parameter does not match the authenticated shop are now rejected with 401. `current_shopify_domain` no longer reflects the `shop` parameter; use `requested_shopify_domain` when you need the requested/bootstrap shop value. [#2081](https://github.com/Shopify/shopify_app/pull/2081)
- Harden embedded app host validation to prevent parser-differential open redirects. [#2078](https://github.com/Shopify/shopify_app/pull/2078)

23.0.2 (May 22, 2026)
----------
- Validate host param in generated HomeController template to prevent open redirect [#2059](https://github.com/Shopify/shopify_app/pull/2059)
```

**File:** test/controllers/callback_controller_test.rb (L125-138)
```ruby
    test "#callback returns to root if the host in the param doesn't match configuration indicating a potential phishing attack" do
      host = "hackerman-evil-site.com/hide-yo-wife-hide-yo-kids"
      encoded_host = Base64.strict_encode64(host + "/admin")
      hacker_params = @callback_params.dup
      hacker_params[:host] = encoded_host
      ShopifyAPI::Auth::Oauth::AuthQuery.stubs(:new).with(**hacker_params).returns(@auth_query)
      ShopifyAPI::Auth::Oauth.expects(:validate_auth_callback).returns({
        cookie: @stubbed_cookie,
        session: @stubbed_session,
      })

      get :callback, params: hacker_params
      assert_redirected_to ShopifyApp.configuration.root_url
    end
```
