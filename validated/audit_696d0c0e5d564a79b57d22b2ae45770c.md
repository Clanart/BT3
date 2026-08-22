### Title
App Proxy signed requests never expire — HMAC verification omits timestamp/deadline check - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification` validates that an incoming app-proxy request's `signature` query parameter matches an HMAC computed over the request's other query parameters (which include a `timestamp`), but it never checks that the `timestamp` is recent. Any previously valid, signed app-proxy URL therefore remains permanently acceptable, mirroring the reported bug class where a signed "commitment"/request's deadline is embedded in the signed payload but never actually enforced.

### Finding Description
`query_string_valid?` extracts the `signature` parameter, recomputes the HMAC over the remaining sorted query parameters (which include `timestamp`), and compares it with `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

`timestamp` is part of the signed data, exactly like the `deadline` field in the Astaria `Commitment.lienRequest.strategy`. In the Astaria bug, the deadline was embedded in the signed strategy payload but `VaultImplementation._validateCommitment()` never compared it to `block.timestamp`, so any signature that was valid at signing time remained valid forever. Here, the same structural flaw exists: `timestamp` is embedded in the signed query string, but at no point does `verify_proxy_request` / `query_string_valid?` / `calculated_signature` compare it against the current time: [2](#0-1) 

Grepping the codebase confirms there is no expiry/staleness check anywhere for app-proxy timestamps outside of the test fixtures that merely reuse a fixed literal timestamp value for HMAC computation, not for freshness validation.

### Impact Explanation
Any app-proxy request URL that was ever validly signed by Shopify (or leaked via browser history, referrer headers, server logs, proxies, or shared links) can be replayed indefinitely by an unrelated attacker without any interaction from the merchant or Shopify. Because `AppProxyVerification` is the sole gate protecting controllers such as the generated `AppProxyController`: [3](#0-2) 
an attacker who obtains one old signed proxy URL gains permanent, unauthenticated access to that endpoint and shop context as if the request were freshly issued by Shopify — an accepted forged (stale) signed request.

### Likelihood Explanation
Exploitation requires only capturing one previously-issued, legitimately-signed app-proxy URL (e.g., from logs, caching proxies, browser history, or a `Referer` leak) — no secrets, credentials, or privileged access are needed, and the request can be replayed by any anonymous third party at any point in the future.

### Recommendation
In `query_string_valid?` (or `verify_proxy_request`), after confirming the signature is valid, additionally parse `timestamp` and reject the request if `Time.now.to_i - timestamp.to_i` exceeds a reasonable window (Shopify's own app-proxy documentation recommends this check).

### Proof of Concept
1. A merchant's storefront makes a legitimate app-proxy request to `/apps/my-app?...&timestamp=1466106083&signature=<valid_hmac>`. The signature is valid and accepted, per the existing test: [4](#0-3) 
2. An attacker captures this exact URL (e.g., from a `Referer` header logged by a third-party analytics/CDN service, from browser history, or a proxy/log).
3. Years later, the attacker replays the identical URL. `query_string_valid?` recomputes the same HMAC over the same query parameters and returns `true`, because `timestamp` is never compared to the current time — the request is accepted exactly as it would be for a genuinely fresh request.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L1-27)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module AppProxyVerification
    extend ActiveSupport::Concern
    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_proxy_request
    end

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

**File:** lib/generators/shopify_app/app_proxy_controller/templates/app_proxy_controller.rb (L1-9)
```ruby
# frozen_string_literal: true

class AppProxyController < ApplicationController
  include ShopifyApp::AppProxyVerification

  def index
    render(layout: false, content_type: "application/liquid")
  end
end
```

**File:** test/shopify_app/controller_concerns/app_proxy_verification_test.rb (L61-72)
```ruby
  test "request with a valid signature should pass" do
    with_test_routes do
      valid_params = {
        shop: "some-random-store.myshopify.com",
        path_prefix: "/apps/my-app",
        timestamp: "1466106083",
        signature: "f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd",
      }
      get :basic, params: valid_params
      assert_response :ok
    end
  end
```
