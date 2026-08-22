### Title
Missing Timestamp Freshness/Staleness Check in App Proxy Signature Verification Enables Unlimited Replay of Signed Requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` verifies that the `signature` query parameter matches an HMAC computed over the other query parameters (which include a `timestamp` field), but it never checks that the `timestamp` value is recent.

### Finding Description
`verify_proxy_request` calls `query_string_valid?`, which recomputes the HMAC signature over all query parameters (including `timestamp`) and compares it to the supplied `signature` using `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

The `timestamp` parameter is included in the data that is signed, but its value is never read or validated against the current time anywhere in this module — it is treated purely as opaque signed data, not as a freshness/expiry marker.

This mirrors the reported bug class: a signed payload (the Chainlink price feed round data / here, the app-proxy query string) is validated for integrity (non-zero price / correct HMAC) but not for freshness (`updatedAt` / `timestamp`). Once a valid signed app-proxy query string is observed — e.g., via browser history, a `Referer` header, shared links, proxy/CDN access logs, or network capture — it remains valid forever, because `query_string_valid?` has no notion of expiry.

### Impact Explanation
Any party who obtains a previously valid signed app-proxy URL (which is not treated as a secret and can leak through browser history, logs, or referrers) can replay it against the app-proxy endpoint indefinitely and have it accepted as an authentic, freshly-signed Shopify request. This is a concrete "accepted forged/stale signed request" condition: the controller has no way to reject an old, replayed signature, undermining the authenticity guarantee that `AppProxyVerification` is meant to provide for `ShopifyApp::AppProxyVerification`-protected endpoints.

### Likelihood Explanation
Exploitation requires only capturing one previously-issued valid signed app-proxy URL (no secret key, no privileged access needed) and replaying the same query string at any later time; this is directly reachable by any unrelated/anonymous actor via the public app-proxy endpoint, matching the "unprivileged... app-proxy... HMAC verification" scope.

### Recommendation
Parse and validate the `timestamp` query parameter in `query_string_valid?` (or `verify_proxy_request`), rejecting requests whose `timestamp` is outside an acceptable freshness window (e.g., a few minutes), in addition to the existing signature check, analogous to adding a `priceStalenessThreshold` check in the referenced Chainlink fix.

### Proof of Concept
1. Attacker observes a legitimately signed app-proxy request URL, e.g. `?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>` (captured from logs/referrer/history).
2. At any later time, the attacker replays the exact same query string to the app-proxy-protected controller.
3. `query_string_valid?` recomputes the HMAC over the same parameters (including the stale `timestamp`) and it matches `signature`, so `verify_proxy_request` allows the request through with no freshness check, as shown by the passing test cases that only validate signature correctness, never staleness: [2](#0-1)

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
