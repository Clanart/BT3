### Title
App Proxy HMAC verification never checks `timestamp` freshness, allowing indefinite replay of captured signed requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates only that the HMAC `signature` matches the other query parameters; it never inspects or bounds-checks the `timestamp` parameter that Shopify includes in every signed app-proxy request.

### Finding Description
`query_string_valid?` parses the query string, extracts `signature`, and recomputes the HMAC over the remaining parameters (including `timestamp`) using the app secret, comparing it via `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

The signature-comparison result is the only criterion used by `verify_proxy_request` to accept or reject the request: [2](#0-1) 

There is no code anywhere in this module (or in `AppProxyVerification`'s callers, e.g. the generated `AppProxyController`) that reads `timestamp` and compares it to `Time.now` with any tolerance window. The gem's own test suite demonstrates this: a query string carrying `timestamp=1466106083` (a 2016 Unix timestamp) is asserted as `valid` today, showing that arbitrarily old signed query strings are, and will forever be, accepted as long as the signature matches: [3](#0-2) [4](#0-3) 

This is the direct analog of the reported "stale Chainlink data" bug class: a value that is supposed to convey freshness (`updatedAt` in Chainlink, `timestamp` in the Shopify App Proxy signature scheme) exists in the payload but is never checked against a tolerance window before the payload is trusted.

### Impact Explanation
Because `secret` is static (it does not rotate per request), any app-proxy query string with a valid signature — once observed by an attacker (e.g., via browser history, proxy/CDN logs, Referer leakage, shared network capture, or a compromised intermediary) — remains permanently valid. An attacker can replay the exact same request indefinitely to any controller mixing in `ShopifyApp::AppProxyVerification`, causing the app to treat forged/old requests as authentic app-proxy traffic from Shopify. Depending on what the app-proxy endpoint does with the `shop` and other query parameters (e.g., trusting `shop` for shop-scoped rendering or state changes), this enables cross-shop/replay abuse of app-proxy endpoints without any interaction from Shopify itself.

### Likelihood Explanation
Exploitation requires only capturing one previously valid app-proxy query string (which is sent over unauthenticated GET requests with a signature in cleartext query params, so it can leak through logs, browser history, or referrer headers) and no active oracle/timing challenge on the server side to prevent replay.

### Recommendation
Read and validate the `timestamp` query parameter in `query_string_valid?`/`verify_proxy_request`, rejecting requests where `Time.now.to_i - timestamp.to_i` exceeds an acceptable tolerance (mirroring the recommendation for stale price feeds: enforce a bounded freshness window), e.g.:
```ruby
def query_string_valid?(query_string)
  query_hash = Rack::Utils.parse_query(query_string)
  signature = query_hash.delete("signature")
  return false if signature.nil?
  return false unless timestamp_recent?(query_hash["timestamp"])

  ActiveSupport::SecurityUtils.secure_compare(calculated_signature(query_hash), signature)
end
```

### Proof of Concept
1. Capture (or construct with a valid secret, e.g. via logs/browser history) a previously valid signed app-proxy query string, e.g. the one used in the existing test suite:
`shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd` [4](#0-3) 
2. Replay this exact query string against any controller including `ShopifyApp::AppProxyVerification` years after the original `timestamp` (2016).
3. `query_string_valid?` recomputes the same HMAC and `secure_compare` succeeds, so `verify_proxy_request` allows the request through with no rejection, regardless of how stale `timestamp` is. [5](#0-4)

### Citations

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
