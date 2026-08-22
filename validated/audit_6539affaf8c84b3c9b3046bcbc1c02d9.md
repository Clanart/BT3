### Title
Inconsistent host-parameter validation in OAuth callback redirect allows parser-differential open redirect - ([File: app/controllers/shopify_app/callback_controller.rb])

### Summary
The `CallbackController#redirect_to_app` flow validates the `host` param for phishing/open-redirect purposes using a different, weaker code path than the one used by `EmbeddedApp#redirect_to_embed_app_in_admin`, which was explicitly hardened against "parser-differential open redirects" per the changelog entry for PR #2078 ("Harden embedded app host validation to prevent parser-differential open redirects").

### Finding Description
`EmbeddedApp#safe_embedded_app_url` decodes the `host` param with `Base64.decode64` and runs it through `unsafe_embedded_host?`, which explicitly rejects control characters, backslashes, and `@` in the authority component, before calling `ShopifyApp::Utils.sanitize_shop_domain`: [1](#0-0) 

By contrast, `CallbackController` computes `decoded_host` via `ShopifyAPI::Auth.embedded_app_url(params[:host])` and validates it with its own `deduced_phishing_attack?`, which only does `URI(decoded_host).host` (Ruby's stdlib `URI`) and feeds that into `sanitize_shop_domain` (which internally uses `Addressable::URI`): [2](#0-1) 

This means two different, independently-maintained code paths exist in the same gem for validating the identical `host` parameter used for post-OAuth redirects — one hardened against control characters/backslash/userinfo-based parser confusion, the other relying only on `URI()` + `sanitize_shop_domain` without the `unsafe_embedded_host?` pre-filtering. Since Ruby's stdlib `URI` and `Addressable::URI` (used inside `sanitize_shop_domain`) can disagree on how ambiguous strings (e.g., strings containing backslashes, control characters, or `userinfo@host` constructs) are parsed into a `host` component, an attacker could potentially craft a `host` value that `URI(decoded_host).host` resolves to a value accepted by `sanitize_shop_domain` while the "real" URL that gets used for the redirect (`"#{decoded_host}#{return_to}"`) points elsewhere.

This is a direct analog to the referenced report's "incorrect parameters" bug class: a security-relevant function (`deduced_phishing_attack?`) is fed the wrong/insufficiently-processed parameter representation (`URI(decoded_host).host` instead of the same hardened, sanitized value used elsewhere), leading to inconsistent enforcement of the same security control across two code paths in the same trust boundary (OAuth callback redirect).

### Impact Explanation
If parser-differential bypass is achievable, an unauthenticated or unrelated party who can trigger the OAuth callback with a crafted `host` parameter could cause `redirect_to_app` to issue an "embedded app" redirect (`allow_other_host: true`) to an attacker-controlled origin instead of the legitimate Shopify admin, resulting in an open redirect immediately after a real OAuth authorization completes — a classic vector for token/session theft via phishing pages that look like the post-install redirect target.

### Likelihood Explanation
Exploitability is contingent on finding an actual string where Ruby's `URI` and `Addressable::URI` disagree on host extraction in a way that both (a) passes `deduced_phishing_attack?`'s check and (b) causes the final concatenated redirect target `"#{decoded_host}#{return_to}"` to resolve to attacker infrastructure. I was not able to confirm a concrete parser-differential payload within the available tools (no shell/browser access to fuzz `URI` vs `Addressable::URI` behavior, and the `Shopify/shopify_app` upstream repository/PR #2078 diff was not accessible to me to confirm exactly what class of payload the hardening in `embedded_app.rb` was designed to stop). The presence of a dedicated, more defensive implementation in `embedded_app.rb` strongly suggests upstream maintainers identified and fixed a real parser-differential issue for the `host` param — the concern here is that `callback_controller.rb` still contains what appears to be the pre-fix logic for the same parameter, not that I have proven a working bypass string.

### Recommendation
Unify host-parameter validation for both `EmbeddedApp#redirect_to_embed_app_in_admin` and `CallbackController#redirect_to_app`/`deduced_phishing_attack?` so they use the same hardened decoding and validation routine (`unsafe_embedded_host?` + `sanitize_shop_domain`), ideally by extracting the logic in `embedded_app.rb` into a single shared helper (e.g. in `ShopifyApp::Utils`) and having `CallbackController` call it instead of maintaining its own separate `decoded_host`/`deduced_phishing_attack?` implementation based on `URI()`.

### Proof of Concept
Not confirmed with a working exploit string. To validate this finding, a Devin session with shell access should:
1. Enumerate strings where `URI(s).host` (Ruby stdlib) and `Addressable::URI.parse(s).host` (used inside `ShopifyApp::Utils.sanitize_shop_domain`) diverge (e.g., inputs containing backslashes, control characters, or `user@host` authority forms — the same classes explicitly filtered by `unsafe_embedded_host?` in `embedded_app.rb`).
2. Base64-encode such a candidate `host` value and drive it through `ShopifyAPI::Auth.embedded_app_url` to see what `decoded_host` becomes.
3. Confirm whether `CallbackController#deduced_phishing_attack?` returns `false` (i.e., accepts) for that value while the resulting `redirect_to(redirect_to, allow_other_host: true)` target in `redirect_to_app` actually points to a non-Shopify host.

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
