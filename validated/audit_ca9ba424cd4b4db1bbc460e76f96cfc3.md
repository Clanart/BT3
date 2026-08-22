### Title
Missing timestamp freshness/expiry check in App Proxy signature verification allows indefinite replay of captured signed requests - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#verify_proxy_request` only checks HMAC correctness of the full query string via `query_string_valid?`, and never validates the `timestamp` parameter against the current time. Any previously valid, fully-signed app-proxy query string (signature + params) therefore remains valid forever and can be replayed by an attacker who observed it once.

### Finding Description
`verify_proxy_request` calls `query_string_valid?(request.query_string)`, which parses the query string, extracts `signature`, and recomputes the HMAC over the remaining sorted params (including `timestamp`) using `calculated_signature`, then compares with `ActiveSupport::SecurityUtils.secure_compare`. [1](#0-0) 

`timestamp` is treated as just another opaque key=value pair folded into the signed string — it is never compared against `Time.now` or any expiry window. As long as the attacker resubmits the *exact same* `query_string` (same `shop`, `timestamp`, `path_prefix`, etc., and same `signature`), `calculated_signature` recomputes an identical HMAC and `secure_compare` returns true, regardless of how much time has elapsed since the original request was issued. There is no other before_action or check in this concern (or documented usage of it) that enforces recency; this is the sole gate in front of the controller action.

Attack flow:
1. Attacker observes one legitimate app-proxy request URL (e.g., from browser history, shared link, proxy/CDN logs, or a public storefront page that embeds the proxied endpoint) containing `shop`, `timestamp`, `signature`, and any other params.
2. Attacker replays the identical GET request (same query string) at any later time — even after the original timestamp is arbitrarily stale.
3. `query_string_valid?` recomputes the same HMAC over the same param set and the signature still matches, so `head(:forbidden)` is never triggered and the app-proxy action executes as if Shopify had freshly signed it.

### Impact Explanation
This enables unauthenticated, indefinite replay of a captured app-proxy request against the affected shop's app-proxy resources ("Forged app proxy request" / signature-replay class). Depending on what the specific app-proxy action does (e.g., returning shop-scoped data, triggering a mutating action, or acting on behalf of the shop for that specific request), the attacker can repeatedly invoke that exact action as that shop without ever needing Shopify to re-sign the request, since the check binds only to HMAC correctness and not to a bounded validity window.

### Likelihood Explanation
Feasibility is straightforward for any request that leaks a valid signed query string once (browser history, referrer leaks, logs, caching proxies, or a link shared/publicly indexed). No secrets, tokens, or elevated privileges are needed — only the previously observed query string, which by design becomes a static, indefinitely-reusable credential enabling exactly the action it was originally signed for.

### Recommendation
Add an explicit freshness check comparing the `timestamp` query parameter to `Time.now.to_i` with a bounded tolerance window (e.g., reject if `|Time.now.to_i - timestamp.to_i| > ALLOWED_SKEW`) before or in addition to the signature comparison in `query_string_valid?`.

### Proof of Concept
```ruby
test "replay of a previously valid, stale app proxy request is still accepted" do
  travel_to Time.zone.at(1_000_000_000) do
    get "/proxy_route", params: { shop: "shop.myshopify.com", timestamp: "1000000000" }
    # capture the fully signed URL, e.g. via signed_params helper
    @captured_query_string = request.query_string
  end

  travel_to Time.zone.at(1_000_000_000 + 10_000_000) do # far in the future
    get "/proxy_route?#{@captured_query_string}"
    assert_response :success # currently passes -- should be :forbidden due to stale timestamp
  end
end
```
This demonstrates that `verify_proxy_request` returns 200 for a stale, previously-captured `query_string+signature` pair instead of 403, confirming absence of a freshness/expiry check in `query_string_valid?`. [2](#0-1)

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
