### Title
App Proxy request signature verification accepts expired timestamps, permitting unlimited replay of a captured signed request - (File: lib/shopify_app/controller_concerns/app_proxy_verification.rb)

### Summary
The Sherlock finding for Astaria (M-18) is that a signed off-chain commitment lacks a nonce check, so a strategist cannot invalidate it and it can be replayed to re-borrow funds repeatedly. The structural analog in `shopify_app` is `ShopifyApp::AppProxyVerification#query_string_valid?`, which validates the HMAC `signature` of an app-proxy request but never validates the `timestamp` parameter that Shopify includes in every signed app-proxy request. Because the signature itself never expires and the gem performs no freshness/replay check, any request that was ever validly signed by Shopify (and observed by an attacker, e.g. via browser history, referrer leakage, proxy logs, or a shared/misconfigured CDN cache) remains permanently "valid" and can be replayed against the app indefinitely.

### Finding Description
`ShopifyApp::AppProxyVerification` is included by an app's proxy controller to authenticate that a request truly originated from Shopify's app-proxy feature: [1](#0-0) 

The verification logic only:
1. Extracts the `signature` query param.
2. Recomputes an HMAC over the remaining sorted query params (which include `shop`, `path_prefix`, and `timestamp`).
3. Uses `ActiveSupport::SecurityUtils.secure_compare` to check the signature matches. [2](#0-1) 

Nowhere in this method (or anywhere else in the concern) is `timestamp` compared against the current time or checked to be within a valid window — the test suite even shows a `timestamp` value of `1466106083` (year 2016) being accepted as "valid" with no time-based rejection: [3](#0-2) 

Because `timestamp` is just another signed field with no server-side freshness check, the HMAC signature is a *static, non-expiring token* for that exact query string. This mirrors the Astaria bug class precisely: a signed authorization is meant to be scoped/limited (there, by a strategist nonce; here, by request recency), but the verifying code omits that check, so the signed artifact can be reused (replayed) an unbounded number of times.

Contrast this with `WebhookVerification`/`PayloadVerification`, which HMAC the full raw POST body (making blind replay less useful for state-changing effects unless the exact webhook is re-sent) — the app-proxy path is worse because it is a simple GET whose entire "authorization" is a small, guessable-shaped query string that is exposed in URLs (logs, Referer headers, browser history, shared links) far more readily than a POST body. [4](#0-3) 

### Impact Explanation
Any captured/leaked valid app-proxy URL (which necessarily reveals `shop`, `path_prefix`, `timestamp`, and `signature` in plaintext, since it's a GET request) can be replayed by an unrelated/unauthenticated party at any time in the future, causing the app to treat the request as an authentic Shopify-originated proxy call for that shop indefinitely. Depending on what the app's proxy controller does with this trusted request (e.g. exposing merchant-scoped data, triggering purchases, loyalty actions, or other business logic gated only on "this came from Shopify for `shop=X`"), this enables cross-shop/cross-session replay attacks with no way for the app to invalidate the request short of rotating `config.secret` for all shops.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to first observe a legitimately signed app-proxy URL (via logs, browser history/autofill, Referer header leakage to third-party resources loaded within the proxied page, shared screenshots/URLs, or a compromised network intermediary). Once obtained, exploitation requires no privileges, no valid session, and no interaction with the merchant — a simple unauthenticated HTTP GET replay of the captured URL. There is no rate limiting or dedupe/nonce store in the gem to prevent this.

### Recommendation
Add a freshness check comparing `timestamp` from the query string to `Time.now.to_i`, rejecting requests outside an acceptable window (e.g. Shopify's own guidance of a few minutes), before or in addition to the signature comparison in `query_string_valid?`:

```ruby
def query_string_valid?(query_string)
  query_hash = Rack::Utils.parse_query(query_string)

  signature = query_hash.delete("signature")
  return false if signature.nil?

  return false unless timestamp_fresh?(query_hash["timestamp"])

  ActiveSupport::SecurityUtils.secure_compare(
    calculated_signature(query_hash),
    signature,
  )
end

def timestamp_fresh?(timestamp)
  return false if timestamp.blank?
  (Time.now.to_i - timestamp.to_i).abs <= ShopifyApp.configuration.app_proxy_timestamp_tolerance
end
```

This closes the replay window analogous to how the Astaria fix recommended enforcing/incrementing a nonce to invalidate stale signed commitments.

### Proof of Concept
1. An app implements a proxy controller with `include ShopifyApp::AppProxyVerification`, as shown in the generator template.
2. Shopify sends a legitimate proxied request such as:
   `GET /apps/my-app?shop=some-shop.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd`
3. This exact URL, once observed by any third party (browser history, access logs, `Referer` leakage from an embedded resource, etc.), can be replayed verbatim years later.
4. `query_string_valid?` recomputes the same HMAC over the same query params (excluding `signature`) and it still matches, because `timestamp` is never checked for freshness — confirmed by the existing test asserting this exact stale timestamp/signature pair as "valid": [3](#0-2) 
5. `verify_proxy_request` therefore allows the request through (`head(:forbidden)` is never called), and the app's controller action executes as if Shopify had just sent it. [5](#0-4)

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L1-38)
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

    def calculated_signature(query_hash_without_signature)
      sorted_params = query_hash_without_signature.collect { |k, v| "#{k}=#{Array(v).join(",")}" }.sort.join

      OpenSSL::HMAC.hexdigest(
        OpenSSL::Digest.new("sha256"),
        ShopifyApp.configuration.secret,
        sorted_params,
      )
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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L15-21)
```ruby
    def verify_request
      data = request.raw_post
      unless hmac_valid?(data)
        ShopifyApp::Logger.debug("Webhook verification failed - HMAC invalid")
        head(:unauthorized)
      end
    end
```
