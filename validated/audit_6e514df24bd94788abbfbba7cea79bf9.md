### Title
Missing replay/expiry protection in app proxy signature verification allows indefinite replay of captured signed requests - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` only validates that the HMAC signature matches the given query parameters; it never checks that the `timestamp` parameter is recent or that the request/signature hasn't been used before. Any previously observed valid app-proxy query string (e.g., leaked via browser history, referrer headers, proxy/access logs) remains permanently valid and can be replayed against the same route.

### Finding Description
`verify_proxy_request` calls `query_string_valid?(request.query_string)`, which parses the query string, extracts the `signature`, and recomputes the HMAC over the remaining sorted parameters using `ShopifyApp.configuration.secret`, comparing with `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

The `timestamp` field is treated only as one more signed parameter contributing to the HMAC input — there is no logic anywhere in this method, or in `calculated_signature`, that compares `timestamp` to the current time or enforces any window of validity: [2](#0-1) 

Because the signature is a deterministic function of the exact query parameters (including `timestamp`), any exact captured query string will produce the same valid signature forever. Once an attacker has observed one such full valid query string for a route with side effects, they can replay it byte-for-byte at any point in the future and `query_string_valid?` will return `true`, causing `verify_proxy_request` to allow the request through. There is no nonce store, no timestamp-age check, and no other before_action in the concern that would reject a stale request — the only gate is signature correctness, not freshness.

### Impact Explanation
This matches Shopify's "forged app-proxy request accepted" impact class: an app proxy endpoint that performs a state-changing action (e.g., placing an order side-effect, toggling a setting, writing data) tied to a shop can be triggered again at will by anyone who captured one legitimate request, without any interaction from the shop or without needing the app secret. This is a replay-based unauthorized action execution impersonating a legitimate shop's identity, scoped to whatever action the specific app-proxy route performs.

### Likelihood Explanation
Requires only one passively observed valid signed app-proxy URL (browser history, network logs, Referer leakage, shared/forwarded links, or a public proxy/CDN log) — no cryptographic secret or privileged access needed. Once obtained, the replay is trivially repeatable indefinitely since there's no expiry or one-time-use enforcement.

### Recommendation
In `query_string_valid?` (or `verify_proxy_request`), after signature verification succeeds, additionally parse `query_hash["timestamp"]` and reject the request if it falls outside an acceptable freshness window (e.g., a few minutes), matching Shopify's documented app-proxy timestamp semantics. Optionally track/consume nonces or timestamps for stronger single-use guarantees on sensitive routes.

### Proof of Concept
```ruby
test "stale but validly-signed request should be rejected as expired" do
  with_test_routes do
    old_timestamp = (Time.now.to_i - 30 * 24 * 60 * 60).to_s # 30 days old
    params_without_sig = { shop: "some-random-store.myshopify.com",
                            path_prefix: "/apps/my-app",
                            timestamp: old_timestamp }
    signature = OpenSSL::HMAC.hexdigest(
      OpenSSL::Digest.new("sha256"),
      "secret",
      params_without_sig.collect { |k, v| "#{k}=#{v}" }.sort.join,
    )
    get :basic, params: params_without_sig.merge(signature: signature)
    # Currently returns :ok because only signature validity is checked;
    # expected behavior should be :forbidden due to stale timestamp.
    assert_response :ok # demonstrates the bug — should be :forbidden
  end
end
```
This replays the same fully-signed query indefinitely because [3](#0-2)  never inspects `timestamp` age before accepting the signature.

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
