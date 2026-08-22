### Title
App Proxy request signature verification accepts arbitrarily stale/replayed requests due to missing `timestamp` freshness check - (File: lib/shopify_app/controller_concerns/app_proxy_verification.rb)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates only the HMAC `signature` of an app-proxy request query string and never checks the `timestamp` parameter that Shopify includes in every signed app-proxy request for freshness. This mirrors the reported oracle bug class: a value that carries a timestamp intended to bound its validity (Chainlink's `updatedAt` / Shopify's `timestamp`) is accepted and trusted without any staleness check, allowing arbitrarily old signed data to be used as if it were current.

### Finding Description
`query_string_valid?` parses the query string, extracts and removes the `signature`, recomputes the HMAC over the remaining sorted parameters (which includes `timestamp`), and compares it with `ActiveSupport::SecurityUtils.secure_compare`. [1](#0-0) 
The `timestamp` value is only used as signed input material for the HMAC digest, not as a freshness bound; there is no comparison against the current time anywhere in this module or in the `verify_proxy_request` before_action. [2](#0-1) 
Because the signature is a deterministic function of the full query string (including `timestamp`), any request that was validly signed by Shopify remains permanently valid and replayable — there is no mechanism analogous to Chainlink's `updatedAt` tolerance check that would reject a request whose `timestamp` is older than an acceptable window.

### Impact Explanation
Any app-proxy request (and its `signature`) that is ever observed by an intermediary (browser history, logs, proxies, referrer leakage, shared links, etc.) can be replayed indefinitely against the app's proxy endpoint without triggering a `403`, since the verification path only checks signature validity, not recency. For app-proxy endpoints that perform shop-context actions or return shop-scoped data based solely on this verification, this enables cross-time/cross-context replay analogous to using "stale oracle data as if fresh" in the reference report — the trust decision (`forbidden` vs. allowed) is made without bounding the age of the signed data.

### Likelihood Explanation
Exploitation only requires capturing one previously valid, Shopify-signed app-proxy request URL (these are often visible in browser address bars, referrer headers, or server logs since they're issued via GET requests through the storefront). No secret key or privileged access is needed to replay it — the attacker just needs to have observed a single legitimate request/signature pair, which is realistic for publicly-reachable app-proxy paths.

### Recommendation
Add a freshness check comparable to Chainlink's `updatedAt` recommendation: parse the `timestamp` parameter from the query string and reject the request (`head(:forbidden)`) if `Time.now.to_i - timestamp.to_i` exceeds an acceptable tolerance window (e.g., a few minutes), in addition to the existing HMAC signature check in `query_string_valid?`.

### Proof of Concept
1. A legitimate app-proxy request is issued by Shopify to the app, e.g.
`GET /apps/my-app?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid_hmac>`
2. `verify_proxy_request` calls `query_string_valid?`, which only recomputes and compares the HMAC; it never checks that `timestamp=1466106083` is close to `Time.now`. [1](#0-0) 
3. As confirmed by the existing test suite, the exact same query string (with its original, long-past `timestamp`) is accepted as valid as long as the signature matches — no expiry is enforced. [3](#0-2) 
4. An attacker who has ever observed this signed URL can replay it against the app-proxy endpoint at any later time, and it will pass verification unconditionally.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L11-13)
```ruby
    def verify_proxy_request
      head(:forbidden) unless query_string_valid?(request.query_string)
    end
```

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
