### Title
Missing timestamp freshness check in `AppProxyVerification#query_string_valid?` allows indefinite replay of a validly-signed App Proxy request - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` verifies only that the HMAC `signature` over the query string matches, but never checks that the `timestamp` parameter included in that same signed payload is recent. This mirrors the reported bug class: a signed payload carries a timestamp intended to prove freshness, but the verifier never inspects it, so stale/old signed data is accepted exactly like fresh data.

### Finding Description
`query_string_valid?` extracts every query parameter (including `timestamp`), removes `signature`, recomputes the HMAC over the remaining sorted `key=value` pairs, and compares it to the supplied `signature`: [1](#0-0) 

The `timestamp` field is part of the signed data (as shown by the tests using `timestamp=1466106083`), but nothing in `query_string_valid?` or `verify_proxy_request` compares this timestamp against the current time or enforces any maximum age: [2](#0-1) 

As a result, any request whose full query string (including the old `timestamp`) and matching `signature` were once valid remains valid forever, since the HMAC comparison is the only gate.

### Impact Explanation
If an App Proxy request URL (which necessarily traverses the network from Shopify to the merchant's app) is ever observed by a third party — e.g., via server logs, browser history, a referrer header, proxy/CDN logs, or a compromised intermediary — that exact query string can be replayed indefinitely against the app's App Proxy endpoint and will be treated by `verify_proxy_request` as a fresh, legitimately Shopify-signed request. If the App Proxy controller action performs any state-changing operation (order actions, discount application, cart mutation, etc.) rather than a pure read, this enables an unauthenticated replay-based forgery of a signed request with no bound on how long after the original request it can be reused.

### Likelihood Explanation
Exploitation requires the attacker to have captured a previously valid signed App Proxy query string; it does not require possession of `ShopifyApp.configuration.secret`. This is a plausible scenario in shared-logging, browser-history, or proxy-caching environments common to storefront traffic exposed through App Proxy links.

### Recommendation
In `query_string_valid?`, after successfully verifying the signature, additionally parse `timestamp` and reject the request if `Time.now.to_i - timestamp.to_i` exceeds a bounded threshold (e.g., a few minutes), returning `false`/`head(:forbidden)` for stale timestamps, mirroring the recommended fix of checking a signed payload's freshness rather than trusting the signature alone.

### Proof of Concept
1. Capture (or otherwise obtain) a previously issued, validly signed App Proxy request URL, e.g.
   `GET /apps/my-app?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid_hmac>`
2. Replay the identical URL against the app's App Proxy endpoint at any later time.
3. `verify_proxy_request` → `query_string_valid?` recomputes the same HMAC over the same query parameters (including the stale `timestamp`) and it matches `signature`, so the request passes verification with `head(:ok)` regardless of how old `timestamp` is, as demonstrated by the existing test asserting a fixed old timestamp/signature pair is accepted: [3](#0-2)

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L11-13)
```ruby
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
