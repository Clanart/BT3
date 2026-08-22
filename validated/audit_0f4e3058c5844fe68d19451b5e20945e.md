### Title
Open redirect / phishing bypass in OAuth callback `host` validation due to parser-differential check not hardened like the embedded-app redirect path - (File: app/controllers/shopify_app/callback_controller.rb)

### Summary
The reported bug class is an asymmetry between two sibling functions that are supposed to enforce the same security invariant, where one function has the full check and the other has a weaker/partial version of it (`safeTransferFrom` vs `safeBatchTransferFrom` blacklist check). The same asymmetry pattern exists in `shopify_app` between `ShopifyApp::EmbeddedApp#safe_embedded_app_url`/`deduced_phishing_attack?` (used for the embed-in-admin redirect) and `ShopifyApp::CallbackController#deduced_phishing_attack?` (used for the post-OAuth redirect). Both methods exist to stop the same "phishing redirect" attack via an attacker-controlled, Base64-encoded `host` param, but only the `EmbeddedApp` version was hardened against parser-differential attacks.

### Finding Description
`ShopifyApp::EmbeddedApp#deduced_phishing_attack?` decodes the `host` param and, before calling `ShopifyApp::Utils.sanitize_shop_domain`, runs it through `unsafe_embedded_host?`, which rejects empty/invalid encodings, control characters, backslashes, and embedded userinfo (`@`) in the authority component: [1](#0-0) 

The CHANGELOG confirms this hardening was added specifically to close a parser-differential open-redirect bug: "Harden embedded app host validation to prevent parser-differential open redirects" [#2078] in version 23.0.3. [2](#0-1) 

However, `ShopifyApp::CallbackController#deduced_phishing_attack?`, which performs the exact same conceptual check (validating that the `host` param used to build a post-OAuth redirect target is a trusted Shopify domain) uses a different, un-hardened code path:
```ruby
def decoded_host
  @decoded_host ||= ShopifyAPI::Auth.embedded_app_url(params[:host])
end

def deduced_phishing_attack?
  sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)
  ...
  sanitized_host.nil?
end
``` [3](#0-2) 

This method extracts the host using Ruby's standard `URI` parser and passes only `URI(...).host` into `sanitize_shop_domain`, with none of the `unsafe_embedded_host?` protections (control-character rejection, backslash rejection, userinfo/`@` rejection) that were added to the `EmbeddedApp` concern. `sanitize_shop_domain` itself internally re-parses the string with `Addressable::URI`: [4](#0-3) [5](#0-4) 

Because `URI(decoded_host).host` (stdlib `URI`) and `Addressable::URI.parse` (used both directly in `sanitize_shop_domain` and, crucially, in the hardened `EmbeddedApp` path) can disagree on what constitutes the "host"/"authority" for malformed strings (e.g., strings containing backslashes, userinfo, or control characters), the `CallbackController` path is exactly the kind of parser-differential surface that PR #2078 was created to close — but that fix was applied only to `EmbeddedApp`, not to `CallbackController`. This mirrors the reported bug class precisely: the "batch" sibling function (`CallbackController#deduced_phishing_attack?`) omits protections present in the "single" sibling function (`EmbeddedApp#deduced_phishing_attack?`).

`redirect_to_app` uses this weaker check to decide whether to redirect to an attacker-influenced host after OAuth completes: [6](#0-5) 

### Impact Explanation
If `URI(...).host` parses an attacker-crafted `host` value differently than `Addressable::URI` (the parser used for the input-validation logic that `unsafe_embedded_host?` was created to compensate for), an attacker can potentially craft a `host` parameter that stdlib `URI` resolves to a value that survives `sanitize_shop_domain`'s trusted-domain check, while `decoded_host` is actually attacker-controlled. Since `redirect_to_app` builds the final redirect target from `decoded_host` (not from the sanitized value) and calls `redirect_to(redirect_to, allow_other_host: true)`, a successful bypass results in the merchant's browser (immediately after completing real OAuth with valid credentials/cookie) being redirected to an attacker-controlled site — the same open-redirect/phishing class the `EmbeddedApp` fix (#2078) explicitly targeted.

### Likelihood Explanation
The `host` parameter to the OAuth callback endpoint is fully attacker/merchant controlled (Base64-encoded, no signature over it), and the callback route is a public unauthenticated endpoint reached mid-OAuth-flow, so exploitability only depends on finding a stdlib-`URI` vs `Addressable::URI` parsing differential — the same class of bug the maintainers already found and fixed once in the sibling `EmbeddedApp` path. I could not fully confirm a concrete byte-for-byte parser-differential payload that both fully passes `sanitize_shop_domain` and yields a different resulting `decoded_host` value, since that requires deeper testing of `Addressable::URI` vs stdlib `URI` behavior across the specific malformed inputs (backslashes, userinfo, control characters, encoding edge cases) that `unsafe_embedded_host?` was written to reject. This uncertainty should be resolved by a Devin session that can execute Ruby to test candidate payloads against both parsers.

### Recommendation
Apply the same `unsafe_embedded_host?`-style validation (rejecting control characters, backslashes, and userinfo in the authority) to `CallbackController#deduced_phishing_attack?`/`decoded_host` before it is used to build the post-OAuth redirect target, or better, have `CallbackController` reuse `ShopifyApp::EmbeddedApp#safe_embedded_app_url`/`deduced_phishing_attack?` directly instead of maintaining a second, divergent implementation of host validation.

### Proof of Concept
Not independently verified with a working payload; the write-up identifies the structural asymmetry (hardened check in `EmbeddedApp` vs un-hardened check in `CallbackController`) as the root cause, consistent with the previously fixed parser-differential bug (#2078) in the sibling code path. Confirming a concrete bypass string requires executing both `URI(...)`/`Addressable::URI.parse` against candidate malformed `host` values, which requires code execution not available in this read-only analysis.

### Citations

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

**File:** CHANGELOG.md (L4-7)
```markdown
23.0.3 (June 24, 2026)
----------
- Token-exchange requests whose `shop` query parameter does not match the authenticated shop are now rejected with 401. `current_shopify_domain` no longer reflects the `shop` parameter; use `requested_shopify_domain` when you need the requested/bootstrap shop value. [#2081](https://github.com/Shopify/shopify_app/pull/2081)
- Harden embedded app host validation to prevent parser-differential open redirects. [#2078](https://github.com/Shopify/shopify_app/pull/2078)
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
