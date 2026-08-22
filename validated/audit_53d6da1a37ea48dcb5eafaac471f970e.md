### Title
App Proxy signature verification has no timestamp/deadline check, allowing indefinite replay of signed proxy requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates that the HMAC `signature` query parameter matches the recomputed signature of the remaining query parameters (including `timestamp`), but it never checks that the `timestamp` value is recent. This mirrors the reported bug class: a "signed" transaction/request is accepted without any freshness/deadline check, so an old, previously-valid signed request remains permanently valid and can be replayed.

### Finding Description
The verification logic only performs a constant-time comparison of the computed HMAC against the supplied `signature`: [1](#0-0) 

The `timestamp` parameter is included in the *signed* payload used to calculate the HMAC, but it is never independently validated against the current time or any expiry window: [2](#0-1) 

This is confirmed by the test suite, which shows that a request signed with a `timestamp` from 2016 (`1466106083`) still passes verification and returns `:ok`, with no expiry enforced: [3](#0-2) 

Because the signature only proves the query string was signed by Shopify at *some point*, and no deadline/freshness check is enforced, any captured, cached, or leaked App Proxy URL (e.g., via server access logs, browser history, shared links, proxy/CDN caches, or a `Referer` header leaked to a third-party asset loaded on the storefront page) remains a valid, "authenticated" request forever. This directly parallels the reported bug class: the check that should invalidate stale signed operations (a deadline) is effectively disabled, allowing outdated requests to be executed unexpectedly.

### Impact Explanation
Any anonymous party that obtains a previously issued App Proxy request URL can replay it at any time in the future and have it accepted by `verify_proxy_request` as if it were a fresh, legitimate request from Shopify on behalf of the shop. If the app-proxy endpoint performs any state-changing action (order lookups, cart modification, price calculation, discount application, etc.) keyed off these query parameters, the attacker can trigger that action repeatedly and indefinitely, using stale parameter values, from an anonymous/unrelated-merchant context. This is analogous to the sandwich/stale-slippage exploitation described in the report — the "signed" evidence of authenticity is disconnected from any temporal validity, enabling misuse of outdated but still-valid-looking signed parameters.

### Likelihood Explanation
Exploitability depends on how the app-proxy endpoint uses the parameters and whether request URLs leak (logs, caching, referrer headers, shared links are all plausible in production). The verification module itself provides no defense-in-depth against replay regardless of downstream endpoint behavior, so the vulnerability is present for every app using `ShopifyApp::AppProxyVerification` as shipped.

### Recommendation
Add an explicit freshness check on the `timestamp` parameter (e.g., reject requests where `Time.now.to_i - timestamp.to_i` exceeds a small tolerance window, such as a few minutes) inside `query_string_valid?` in `lib/shopify_app/controller_concerns/app_proxy_verification.rb`, in addition to the existing HMAC comparison, so that valid-looking but stale signed requests are rejected.

### Proof of Concept
1. Capture (or observe in logs/history/CDN cache) a legitimately Shopify-signed App Proxy request URL, e.g.
 `GET /apps/my-app?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>`
2. At any later time, replay the exact same URL/query string against the app's App Proxy endpoint that includes `ShopifyApp::AppProxyVerification`.
3. `verify_proxy_request` recomputes the HMAC over the unchanged query parameters (including the old `timestamp`) and it matches the supplied `signature`, so the request passes verification exactly as demonstrated by the existing test asserting a 2016 timestamp still returns `:ok`: [3](#0-2) .
4. The endpoint executes the associated action using the stale parameters, with no rejection based on request age.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L17-27)
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
