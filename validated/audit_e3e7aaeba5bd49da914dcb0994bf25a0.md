The strongest reachable analog is in the App Proxy request signature verification, which computes an HMAC over query parameters (including `timestamp`) but never validates that the `timestamp` value is recent, mirroring the Chainlink report's core flaw: a value that exists specifically to signal freshness is accepted without any staleness check.

### Title
Missing timestamp freshness check in App Proxy signature verification enables indefinite replay of captured signed requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` and `#calculated_signature` verify that the HMAC signature over the query string matches, but the `timestamp` parameter included in that signed data is never checked against the current time.

### Finding Description
`verify_proxy_request` calls `query_string_valid?`, which recomputes the HMAC over all query parameters except `signature` (including `timestamp`) and compares it via `ActiveSupport::SecurityUtils.secure_compare`. [1](#0-0) 
Nowhere in this module — or anywhere else in the gem — is the `timestamp` parameter parsed and compared against `Time.now` to reject old requests. [2](#0-1) 
This is structurally identical to the Chainlink bug: a piece of signed data whose only purpose is to convey "freshness" (Chainlink's `updatedAt` / this gem's `timestamp`) is present in the payload but never validated against the current time, so stale/old signed data is treated as equally valid as fresh data.

### Impact Explanation
Any URL that was ever legitimately signed by Shopify for an App Proxy request (which can leak via browser history, server logs, proxies, referrer headers, or a compromised network path) remains valid and will be accepted by the app forever, since there is no expiry enforcement. If any authorization or business-logic decision in the app proxy endpoint depends on the request being "fresh" (e.g., a one-time action, or shop-state assumptions valid only at request time), an attacker who obtains an old signed proxy URL can replay it at any point in the future to trigger that action again as if it were a legitimate, current request from Shopify/the storefront.

### Likelihood Explanation
Exploitability requires an attacker to first obtain a previously valid signed App Proxy URL (e.g., through log access, browser history, or network interception, since App Proxy requests are not TLS-terminated with attacker-controlled paths in the same way admin session tokens are). This is a real but conditional prerequisite, so likelihood is lower than a direct unauthenticated bypass, but the underlying validation gap is unconditional and matches the exact bug class of the reference report (missing staleness/freshness enforcement on signed data).

### Recommendation
Parse the `timestamp` query parameter in `query_string_valid?`/`verify_proxy_request`, and reject the request if `Time.now.to_i - timestamp.to_i` exceeds a small, configurable maximum age (e.g., a few minutes), in addition to the existing HMAC signature check.

### Proof of Concept
1. Obtain any legitimately-signed App Proxy request URL for the app (e.g., `https://example.com/app_proxy?shop=some-shop.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>`), captured from logs, browser history, or a network intermediary.
2. Replay this exact URL against the app at any later time (days, months, or years after it was issued).
3. `AppProxyVerification#verify_proxy_request` recomputes the HMAC over the query parameters and finds it matches, since `timestamp` is only used as HMAC input and never checked against current time — the stale request is accepted as if it were fresh, as shown by the passing test case using a fixed `timestamp: "1466106083"` (a 2016 timestamp) still validating successfully today. [3](#0-2)

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

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L29-37)
```ruby
    def calculated_signature(query_hash_without_signature)
      sorted_params = query_hash_without_signature.collect { |k, v| "#{k}=#{Array(v).join(",")}" }.sort.join

      OpenSSL::HMAC.hexdigest(
        OpenSSL::Digest.new("sha256"),
        ShopifyApp.configuration.secret,
        sorted_params,
      )
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
