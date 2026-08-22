The analog is a genuine inconsistency between how "the current shop" is derived for scope-mismatch checks versus how the authenticated session's shop is actually determined elsewhere in the gem.

### Title
Shop access-scope reauthentication check is computed against the unauthenticated `shop` query parameter instead of the authenticated session's shop - (File: `app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb`)

### Summary
`ShopifyApp::ShopAccessScopesVerification` is meant to force merchants to re-authorize via OAuth whenever the shop's granted access scopes fall out of sync with the scopes configured for the app. However, the concern computes the shop identity used for this comparison from the raw, user-controllable `params[:shop]` query parameter rather than from the shop bound to the already-authenticated session (`current_shopify_session.shop`), which is how the rest of the gem (e.g. `LoginProtection`, `TokenExchange`) derives the authenticated shop for security-relevant decisions.

### Finding Description
`login_on_scope_changes` calls `scopes_mismatch?`, which calls `ShopifyApp.configuration.shop_access_scopes_strategy.update_access_scopes?(current_shopify_domain)`. [1](#0-0) 

Crucially, this concern's private `current_shopify_domain` helper is defined independently of the authenticated session and simply sanitizes the `shop` query param:

```ruby
def current_shopify_domain
  return if params[:shop].blank?

  ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
end
``` [2](#0-1) 

This is the same class of bug as the Lybra finding: two places in the codebase compute what should be the *same* authoritative value ("the shop this request is authenticated for") using two different formulas — one derived from a verified/authenticated source, and one derived from a partial/unverified source. `ShopStrategy.update_access_scopes?` then looks up the *stored* scopes for whatever domain it is given and compares them to the configured scopes: [3](#0-2) 

By contrast, the rest of the gem consistently treats the authenticated shop as the one bound to the loaded session — e.g. `LoginProtection#authenticated_shopify_domain` (`current_shopify_session&.shop`) and `TokenExchange#authenticated_shopify_domain_from_token` (`current_shopify_session&.shop || jwt_shopify_domain`), both of which are derived from a verified cookie/session or verified JWT, never from a raw query parameter. [4](#0-3) [5](#0-4) 

Because `ShopAccessScopesVerification#current_shopify_domain` ignores the actual authenticated session and instead trusts `params[:shop]`, the scope-mismatch gate can be evaluated against a shop the requester does not control and is not authenticated as, decoupling the "is reauth required" decision from the shop whose data the authenticated session will actually be used to access.

### Impact Explanation
An app that includes `ShopifyApp::ShopAccessScopesVerification` in an authenticated controller (as documented) relies on it to force reauthorization when a shop's stored offline-token scopes no longer match the scopes the app requires — this is the only mechanism in the gem enforcing shop-level (offline token) scope freshness (`LoginProtection#activate_shopify_session` only checks user/online scopes via `user_access_scopes_strategy`). [6](#0-5) 

Because the gate is evaluated against `params[:shop]` rather than the session's actual shop, a requester whose real authenticated shop has stale/insufficient scopes can supply a `shop` query parameter pointing at any other shop whose stored scopes happen to match the app's configured scopes (this is entirely observable/guessable since it's just an equality of scope sets, not shop-specific secrets) and the mismatch check silently passes, allowing the request to proceed on the real (out-of-date) authenticated session without forcing OAuth reauthorization. This lets the security control be bypassed by controlling an unrelated, unauthenticated HTTP parameter.

### Likelihood Explanation
The concern is reachable on any endpoint an app author includes it on and is driven entirely by an attacker/merchant-controlled `shop` query parameter with no verification that it matches the authenticated session — no special privilege is required to manipulate this parameter, only reachability of the endpoint via a normal authenticated (or even embedded) app request.

### Recommendation
Compute the shop used for the scope-mismatch comparison from the authenticated session (`current_shopify_session&.shop`) rather than from `params[:shop]`, consistent with how `authenticated_shopify_domain` is computed in `LoginProtection` and `TokenExchange`, so the reauthorization decision is always evaluated for the shop actually bound to the current authenticated request.

### Proof of Concept
1. Authenticate normally to `attacker-shop.myshopify.com`; the app grants an offline token with scopes `read_products` while the app configuration has since been updated to require `read_products,write_discounts` (a legitimate pending scope-mismatch for `attacker-shop`).
2. Send a request to a controller including `ShopifyApp::ShopAccessScopesVerification` with `?shop=another-shop.myshopify.com`, where `another-shop` happens to already have scopes matching the app's current configuration (e.g. a shop that recently reinstalled).
3. `scopes_mismatch?` calls `update_access_scopes?("another-shop.myshopify.com")`, which returns `false` because `another-shop`'s stored scopes equal the configured scopes.
4. `login_on_scope_changes` does not redirect to login, and the request proceeds using the authenticated session for `attacker-shop`, whose actual scopes are stale and should have triggered forced reauthorization.

### Citations

**File:** app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb (L18-38)
```ruby
    def login_on_scope_changes
      if scopes_mismatch?
        if embedded_param?
          redirect_for_embedded
        else
          redirect_to(shop_login)
        end
      end
    end

    private

    def scopes_mismatch?
      ShopifyApp.configuration.shop_access_scopes_strategy.update_access_scopes?(current_shopify_domain)
    end

    def current_shopify_domain
      return if params[:shop].blank?

      ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
    end
```

**File:** lib/shopify_app/access_scopes/shop_strategy.rb (L6-16)
```ruby
      class << self
        def update_access_scopes?(shop_domain)
          shop_access_scopes = shop_access_scopes(shop_domain)
          configuration_access_scopes != shop_access_scopes
        end

        private

        def shop_access_scopes(shop_domain)
          ShopifyApp::SessionRepository.retrieve_shop_session_by_shopify_domain(shop_domain)&.scope
        end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L37-41)
```ruby
      if ShopifyApp.configuration.reauth_on_access_scope_changes &&
          !ShopifyApp.configuration.user_access_scopes_strategy.covers_scopes?(current_shopify_session)
        clear_shopify_session
        return redirect_to_login
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

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L69-71)
```ruby
    def authenticated_shopify_domain_from_token
      current_shopify_session&.shop || jwt_shopify_domain
    end
```
