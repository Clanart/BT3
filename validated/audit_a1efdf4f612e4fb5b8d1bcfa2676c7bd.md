### Title
Missing Replay Protection in App Proxy Request Signature Verification - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` verifies that the HMAC `signature` query parameter matches the app's shared secret, but never validates the freshness of the `timestamp` parameter that Shopify includes in every signed app-proxy request. As a result, a captured/leaked, once-valid signed app-proxy URL remains permanently valid and can be replayed indefinitely.

### Finding Description
`verify_proxy_request` is registered as a `before_action` for any controller that includes `ShopifyApp::AppProxyVerification`, and it is the sole gate protecting app-proxy endpoints: [1](#0-0) 

The actual check, `query_string_valid?`, only strips the `signature` parameter and recomputes the HMAC over the remaining sorted query parameters (which include `shop`, `path_prefix`, `timestamp`, and any request-specific parameters such as a storefront-supplied customer/session identifier): [2](#0-1) 

There is no comparison of the `timestamp` value against the current time, no expiry window, and no nonce/one-time-use tracking. This mirrors the reported bug class exactly: a signed payload (`_request`/`_message`/signature in the original report; here the app-proxy query string + signature) can be replayed an unlimited number of times because the verifying code checks only *authenticity* of the signature, never *freshness* or *uniqueness* of the request.

This is the same design used in the generated `AppProxyController`, so every app built with the default generator inherits the flaw: [3](#0-2) 

The gem's own test suite confirms only signature validity is checked, not staleness — a query string with a `timestamp` from 2016 (`1466106083`) is still accepted as valid as long as the HMAC matches: [4](#0-3) 

### Impact Explanation
Any app-proxy URL that Shopify signs and forwards to the app (these are constructed by Shopify per-request and can end up in browser history, server access logs, proxy logs, referrer headers, or be intercepted on an untrusted network) remains a valid, indefinitely-replayable authenticated request against the merchant's app-proxy endpoints. Since app-proxy requests are the mechanism by which storefront-facing (often unauthenticated, per-customer) data is exchanged with the app backend, replaying an old signed request can let an attacker reissue actions or re-fetch/re-submit data tied to the original request context long after the legitimate request window has passed, with no way for the app to detect or reject the replay. This is a direct, unprivileged-reachable variant of the reported "replay of signed request/message" bug class.

### Likelihood Explanation
Exploitation requires only that an attacker obtain a single previously-valid app-proxy query string (via logs, referrer leakage, shared links, or network interception) — no secret key or elevated privilege is needed. Because the verification code performs no timestamp/staleness or nonce check at all, replay succeeds with certainty once such a URL is obtained, making likelihood high once the (common) leak channels are considered, and reachable directly by an anonymous HTTP client hitting the app-proxy path.

### Recommendation
Add explicit anti-replay protection to `ShopifyApp::AppProxyVerification#query_string_valid?`:
- Reject requests whose `timestamp` parameter is older than a small tolerance window (e.g. a few minutes) from `Time.now`.
- Optionally track consumed `(shop, timestamp, signature)` tuples (or a dedicated nonce) in a short-TTL store to prevent reuse within the validity window.
This mirrors the recommended fix from the original report (nonce/timestamp tracking to prevent replay) applied to the `verify_proxy_request` / `query_string_valid?` methods in `lib/shopify_app/controller_concerns/app_proxy_verification.rb`.

### Proof of Concept
1. Merchant's storefront triggers an app-proxy request; Shopify signs and forwards a URL like:
   `GET /app_proxy/basic?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=f5cd7233...`
2. This URL (or its query string) leaks via server logs, browser history, or a shared link.
3. Any time later (verified by the existing test to still succeed even with a decade-old `timestamp`), an attacker replays the exact same request: [5](#0-4) 
4. `verify_proxy_request` recomputes the HMAC, finds it matches, and allows the request through — with no check on how old the `timestamp` is or whether this exact signature has been used before.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L6-13)
```ruby
    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_proxy_request
    end

    def verify_proxy_request
      head(:forbidden) unless query_string_valid?(request.query_string)
    end
```

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L17-37)
```ruby
    def query_string_valid?(query_string)
      query_hash = Rack::Utils.parse_query(query_string)

      signature = query_hash.delete("signature")
      return false if signature.nil?

      ActiveSupport::SecurityUtils.secure_compare(
        calculated_signature(query_hash),
        signature,
      )
    end

    def calculated_signature(query_hash_without_signature)
      sorted_params = query_hash_without_signature.collect { |k, v| "#{k}=#{Array(v).join(",")}" }.sort.join

      OpenSSL::HMAC.hexdigest(
        OpenSSL::Digest.new("sha256"),
        ShopifyApp.configuration.secret,
        sorted_params,
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

**File:** test/shopify_app/controller_concerns/app_proxy_verification_test.rb (L30-37)
```ruby
  test "basic_query_string" do
    assert query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp="\
      "1466106083&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd")
    assert_not query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp="\
      "1466106083&evil=1&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd")
    assert_not query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-"\
      "app&timestamp=1466106083&evil=1&signature=wrongwrong8b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd")
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
