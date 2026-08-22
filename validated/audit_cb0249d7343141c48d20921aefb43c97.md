### Title
App Proxy signature verification never checks the `timestamp` query parameter for staleness, allowing indefinite replay of a captured signed request - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates that the HMAC `signature` query parameter matches a value computed over all other query parameters (which include a Shopify-supplied `timestamp`), but it never checks that `timestamp` is recent. This mirrors the reported VST Oracle bug class: a freshness/`updatedAt` value is present in the signed data but is never validated against a staleness window, so old signed data is accepted as if it were current.

### Finding Description
The app proxy verification concern computes the expected signature from all query params except `signature` and compares it to the supplied `signature`: [1](#0-0) 

The `timestamp` parameter — which Shopify includes precisely so that signed app-proxy requests can be bound to a point in time — is treated purely as signed payload data, never compared against `Time.now` or any staleness threshold (e.g. the `600` second window recommended for the VST oracle). This is demonstrated directly in the test suite, where a fixed, ancient `timestamp=1466106083` (year 2016) together with its correctly computed `signature` is accepted as valid: [2](#0-1) 

Because the signature depends only on the secret and the query string contents (not on wall-clock time), any request+signature pair captured once (e.g., via browser history, shared/forwarded links, server logs, proxy logs, or network capture) remains cryptographically valid to `verify_proxy_request` forever, since `verify_proxy_request` only calls `query_string_valid?` and does not add any independent expiry check: [3](#0-2) 

### Impact Explanation
An unrelated, unprivileged party who obtains a previously valid, signed app-proxy URL (which frequently ends up in browser history, referrer headers, shared links, or logs, since app-proxy URLs are ordinary GET request URLs surfaced to storefront visitors) can replay that exact URL against the app's app-proxy endpoint at any later time and have it accepted as an authentic, freshly-signed Shopify request. Depending on what the app-proxy action does (e.g., mutate data scoped to the shop/customer identified in the query params, trigger side effects, or return sensitive data), this enables a forged/stale signed request being accepted well beyond its intended validity window — directly matching the "accepted forged signed request" acceptance criterion.

### Likelihood Explanation
Exploitation requires only observing one previously-issued signed app-proxy URL and replaying it via a normal HTTP GET/POST — no secret knowledge, no privileged Shopify session, and no interaction with an app admin is needed. App-proxy URLs are exposed on public storefronts, so capture opportunities (browser history, shared bookmarks, cached pages, proxies/CDNs, logs) are realistic in production use.

### Recommendation
Add an explicit staleness check on the `timestamp` query parameter in `query_string_valid?`/`verify_proxy_request`, e.g.:
```ruby
def timestamp_fresh?(timestamp)
  return false if timestamp.blank?
  (Time.now.to_i - timestamp.to_i).abs <= 600 # 10 minute window, matching Shopify's guidance
end
```
and require both `query_string_valid?(...)` and `timestamp_fresh?(query_hash["timestamp"])` to pass before allowing the request through `verify_proxy_request`.

### Proof of Concept
1. Capture any legitimately signed app-proxy request URL, e.g.
   `GET /apps/my-app?shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd`
2. Replay this exact URL against a controller including `ShopifyApp::AppProxyVerification` at any later date (verified by the existing test asserting `assert_response :ok` for this decade-old timestamp): [2](#0-1) 
3. The request is accepted (`200 OK`) despite the `timestamp` being years stale, because `query_string_valid?` never inspects it for freshness: [4](#0-3)

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L11-37)
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
