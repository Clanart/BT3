### Title
App Proxy Signed Request Replay Due to Missing Timestamp Freshness Check - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#verify_proxy_request` validates an app-proxy request solely by recomputing the HMAC signature over the query parameters (including `timestamp`) and comparing it to the supplied `signature`. It never checks that the `timestamp` parameter is recent relative to `Time.now`, so a previously valid signed request can be replayed indefinitely.

### Finding Description
`verify_proxy_request` calls `query_string_valid?`, which strips the `signature` param, recomputes an HMAC-SHA256 over the remaining sorted params (one of which is `timestamp`) using the app secret, and does a constant-time comparison against the caller-supplied signature. [1](#0-0) [2](#0-1) 

Although `timestamp` is included in the signed payload (as confirmed by the fixtures in the test suite, e.g. `timestamp=1466106083`), there is no logic anywhere in the module that compares this timestamp to the current time or rejects requests whose timestamp is stale/in the past. [3](#0-2) 

This is the same root-cause bug class as the referenced report: a time-bound value (`timestamp` here, `_startTimestamp`/`_endTimestamp` in VTVLVesting) is accepted and even cryptographically signed over, but the code never enforces that it falls within an expected window relative to `block.timestamp`/`Time.now`. As a result, a value that should express "freshness" or "validity window" has no actual temporal constraint enforced by the contract/controller logic.

### Impact Explanation
Because `timestamp` is never checked for freshness, any previously captured, valid app-proxy request (its full query string + signature) remains permanently valid and can be resent by an unrelated, unauthenticated party to any endpoint protected only by `ShopifyApp::AppProxyVerification`. This allows replay of a merchant/storefront-initiated signed request outside of its intended window — an accepted forged/replayed signed request, which is one of the concrete outcomes this analysis is scoped to accept.

### Likelihood Explanation
App proxy query strings (including `signature` and `timestamp`) are sent over the network (via browser/storefront) as part of normal usage and can be observed in browser history, referrer headers, logs, proxies, or shared links, making capture plausible. Once captured, replay is trivial (no additional secret knowledge needed) and works forever since there is no expiry enforcement in this codebase for app-proxy requests.

### Recommendation
Add an explicit temporal bound check in `verify_proxy_request`/`query_string_valid?`, e.g. reject requests where `Time.at(query_hash["timestamp"].to_i) < Time.now - ALLOWED_SKEW` (a short window, such as a few minutes), similar to how OAuth/webhook flows should bound acceptable time drift. This closes the replay window instead of relying only on signature correctness.

### Proof of Concept
1. Observe/capture a legitimately signed app-proxy request URL, e.g.
`GET /app_proxy/basic?shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd`
(this exact query string is confirmed valid by the existing test fixture). [4](#0-3) 
2. At any later time (days, months later), replay the identical query string/signature to the same controller action.
3. `query_string_valid?` recomputes the same HMAC (since none of the signed values changed) and returns `true`, so `verify_proxy_request` allows the request through with `assert_response :ok`, with no check on `timestamp` age. [5](#0-4)

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
