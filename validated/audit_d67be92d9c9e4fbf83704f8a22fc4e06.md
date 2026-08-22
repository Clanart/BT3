### Title
`shop_login` builds the re-auth redirect from raw, unsanitized `params[:shop]`/`params[:host]` instead of the verified shop domain used for the scope check - (File: app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb)

### Summary
`scopes_mismatch?` correctly calls `update_access_scopes?` with the *sanitized* domain from `current_shopify_domain` (which sanitizes via `ShopifyApp::Utils.sanitize_shop_domain` and returns `nil` on blank/invalid input). However, when a mismatch is detected, `shop_login` builds the redirect target using the **raw, unsanitized** `params[:shop]` and `params[:host]` instead of the value that was actually verified. This decouples what was checked from what is redirected to, and the "fail closed" guard in `shop_login_url` only checks for `nil`, not blank, so an empty-but-present `shop` param slips past the guard.

### Finding Description
In `app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb`: [1](#0-0) 

- `scopes_mismatch?` uses `current_shopify_domain`, which is `nil` unless `params[:shop]` passes `ShopifyApp::Utils.sanitize_shop_domain` — this part correctly fails closed to `nil` for an invalid/blank shop.
- `shop_login`, however, ignores `current_shopify_domain` entirely and instead calls `ShopifyApp::Utils.shop_login_url(shop: params[:shop], host: params[:host], return_to: request.fullpath)` — the **raw** request params, not the sanitized/verified domain.

`shop_login_url` itself only treats `nil` as "absent": [2](#0-1) 

Since Ruby treats an empty string `""` as truthy, `unless shop` does **not** short-circuit when `params[:shop]` is present-but-empty or an arbitrary attacker-supplied string that fails sanitization. In that scenario:
1. `current_shopify_domain` sanitizes `params[:shop]` to `nil` (fails closed for the scope check itself).
2. `scopes_mismatch?` is evaluated against `nil`, which for `ShopStrategy#update_access_scopes?` looks up `retrieve_shop_session_by_shopify_domain(nil)` (no session found) and compares against the configured scopes, typically evaluating to a mismatch, triggering the login redirect branch. [3](#0-2) 
3. `shop_login` then builds the redirect URL using the **raw** (unsanitized, possibly attacker-controlled) `params[:shop]` and `params[:host]` — values that were never verified — because it never reuses `current_shopify_domain`.

The redirect target itself (`ShopifyApp.configuration.login_url`) is the app's own configured login path, so this is not a classic redirect-to-arbitrary-external-host bug, but the `shop`/`host` query parameters attached to that internal `/login` route are attacker-controlled and unverified, which can drive the subsequent OAuth/session flow (`SessionsController#new`) toward a shop/host pair that was never confirmed to match the domain that failed the scope check — violating the stated invariant that "scope checks and the resulting login redirect must be bound to the verified shop, not a raw param."

### Impact Explanation
This does not directly produce an open redirect to an external attacker domain, because `shop_login_url` targets the app's own `login_url` path. The concrete, demonstrable defect is narrower than the full "open redirect / cross-shop reauthorization" framed by the question: the scope-check pathway (`scopes_mismatch?`/`current_shopify_domain`) does fail closed to `nil` for blank/invalid shop input, but the redirect-building pathway (`shop_login`) reuses raw, unverified `params[:shop]`/`params[:host]` rather than the sanitized domain, and its only guard (`unless shop`) fails to reject blank-but-present strings. This is a real inconsistency/bug (violates the "bound to verified shop" invariant) but I could not confirm from this code alone that it escalates to full cross-shop token theft or an external open redirect — that would require tracing `SessionsController#new`'s handling of these params, which is outside this file.

### Likelihood Explanation
Reaching `login_on_scope_changes` only requires an unauthenticated GET to any controller action including `ShopAccessScopesVerification` under the legacy (non-token-exchange) strategy, with an attacker-supplied `shop`/`host`. No secrets or prior session are needed, so the precondition is low-effort and repeatable.

### Recommendation
Make `shop_login` use the same verified/sanitized shop domain (`current_shopify_domain`) that was used for the scope check, not raw `params[:shop]`, and change `shop_login_url`'s guard to `return ShopifyApp.configuration.login_url if shop.blank?` so that blank-but-present shop values are also rejected/fail closed. Additionally, sanitize/validate `host` before embedding it in the redirect.

### Proof of Concept
```ruby
# app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb
class TestController < ApplicationController
  include ShopifyApp::ShopAccessScopesVerification
end

test "shop_login uses raw unsanitized params, not the verified shop domain" do
  get :index, params: { shop: "", host: Base64.strict_encode64("attacker-controlled-host") }
  # current_shopify_domain resolves to nil (fails closed for the scope check)
  # but shop_login builds a redirect using the raw blank "shop" and attacker "host"
  assert_redirected_to(/shop=&host=YXR0YWNrZXItY29udHJvbGxlZC1ob3N0/)
end
```
Expected (secure) behavior: when the sanitized/verified shop is `nil`/blank, the login redirect should also fail closed (either skip redirecting, or drop the unverified `shop`/`host` params) rather than propagating the raw attacker-supplied values into the redirect URL.

### Citations

**File:** app/controllers/concerns/shopify_app/shop_access_scopes_verification.rb (L30-42)
```ruby
    def scopes_mismatch?
      ShopifyApp.configuration.shop_access_scopes_strategy.update_access_scopes?(current_shopify_domain)
    end

    def current_shopify_domain
      return if params[:shop].blank?

      ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
    end

    def shop_login
      ShopifyApp::Utils.shop_login_url(shop: params[:shop], host: params[:host], return_to: request.fullpath)
    end
```

**File:** lib/shopify_app/utils.rb (L29-41)
```ruby
      def shop_login_url(shop:, host:, return_to:)
        return ShopifyApp.configuration.login_url unless shop

        url = URI(ShopifyApp.configuration.login_url)

        url.query = URI.encode_www_form(
          shop: shop,
          host: host,
          return_to: return_to,
        )

        url.to_s
      end
```

**File:** lib/shopify_app/access_scopes/shop_strategy.rb (L7-16)
```ruby
        def update_access_scopes?(shop_domain)
          shop_access_scopes = shop_access_scopes(shop_domain)
          configuration_access_scopes != shop_access_scopes
        end

        private

        def shop_access_scopes(shop_domain)
          ShopifyApp::SessionRepository.retrieve_shop_session_by_shopify_domain(shop_domain)&.scope
        end
```
