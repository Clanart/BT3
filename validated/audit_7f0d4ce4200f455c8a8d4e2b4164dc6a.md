No vulnerability found for this question.

The investigation into `Annirich/shopify_app--005` found that all unprivileged entry points already implement proper access control for the bug class described in the report (missing authorization checks allowing cross-tenant/cross-user actions):

- Webhook receipt (`lib/shopify_app/controller_concerns/webhook_verification.rb`) and payload verification (`lib/shopify_app/controller_concerns/payload_verification.rb`) validate an HMAC using `ActiveSupport::SecurityUtils.secure_compare` before any action is processed. [1](#0-0) 

- App proxy requests (`lib/shopify_app/controller_concerns/app_proxy_verification.rb`) require a valid signature computed over the query string, compared with `secure_compare`. [2](#0-1) 

- Extension verification (`app/controllers/shopify_app/extension_verification_controller.rb`) rejects requests without a valid HMAC. [3](#0-2) 

- For authenticated (session-token/token-exchange) flows, `ShopifyApp::TokenExchange#reject_mismatched_requested_shopify_domain` explicitly rejects any request where a user-supplied `shop`/tenant identifier doesn't match the authenticated shop resolved from the verified ID token, preventing exactly the "attacker-supplied target identity" pattern described in the report. [4](#0-3) 

- `EnsureInstalled`, the only concern that derives shop identity from an unauthenticated, user-controllable `shop` query parameter, is explicitly and repeatedly documented as unauthenticated and unsuitable for accessing shop data or making API calls on another tenant's behalf — it is meant only for pre-auth bootstrap/redirect purposes. [5](#0-4) [6](#0-5) 

None of these paths exhibit the reported bug class (an entry point trusting an attacker-supplied "borrower"/target identity without verifying it against the authenticated caller) in a way that leads to concrete authentication bypass, session/token theft, cross-shop access, forged accepted signed requests, CSRF with state change, or secret disclosure.

### Citations

**File:** lib/shopify_app/controller_concerns/payload_verification.rb (L13-23)
```ruby
    def hmac_valid?(data)
      secrets = [ShopifyApp.configuration.secret, ShopifyApp.configuration.old_secret].reject(&:blank?)

      secrets.any? do |secret|
        digest = OpenSSL::Digest.new("sha256")
        ActiveSupport::SecurityUtils.secure_compare(
          shopify_hmac,
          Base64.strict_encode64(OpenSSL::HMAC.digest(digest, secret, data)),
        )
      end
    end
```

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L11-27)
```ruby
    def verify_proxy_request
      head(:forbidden) unless query_string_valid?(request.query_string)
    end

    private

    def query_string_valid?(query_string)
      query_hash = Rack::Utils.parse_query(query_string)

      signature = query_hash.delete("signature")
      return false if signature.nil?

      ActiveSupport::SecurityUtils.secure_compare(
        calculated_signature(query_hash),
        signature,
      )
    end
```

**File:** app/controllers/shopify_app/extension_verification_controller.rb (L11-16)
```ruby
    def verify_request
      unless hmac_valid?(request.raw_post)
        head(:unauthorized)
        ShopifyApp::Logger.debug("Extension verification failed due to invalid HMAC")
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

**File:** docs/shopify_app/controller-concerns.md (L28-33)
```markdown
## EnsureInstalled — Installation Check Only
Use this concern to verify that the app has been installed on a given shop. It is designed for unauthenticated entry points in embedded apps, such as serving the app shell or redirecting to OAuth.

> ⚠️ **This concern does not authenticate the request.** The shop is resolved from the `shop` query string parameter, which is user-controllable. Do not use this concern to gate access to shop data, access tokens, or Shopify API calls. For authenticated actions, use `EnsureHasSession`.

If the app is not installed for the provided `shop` parameter, the request will be redirected to login or the `embedded_redirect_url`.
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
