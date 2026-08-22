## Analysis Result [1](#0-0) 

### Title
App Proxy request signature verification never checks `timestamp` freshness, allowing indefinite replay of captured signed requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates that the HMAC `signature` over the query string (which includes a `timestamp` parameter) matches, but it never checks that the `timestamp` value is close to the current server time. This mirrors the reported Buffer Protocol bug class: a signed payload that carries a timestamp field is accepted purely on signature validity, with no freshness/staleness check against "now," enabling replay of an old, otherwise-valid signed request.

### Finding Description
The `verify_proxy_request` before_action calls `query_string_valid?`, which parses the query string, extracts the `signature`, and recomputes an HMAC-SHA256 over the remaining sorted parameters (including `timestamp`) using the app secret: [2](#0-1) 

Nowhere in this flow is `query_hash["timestamp"]` compared against `Time.now` or any tolerance window. As soon as Shopify (or anyone with a copy of the URL — e.g., via browser history, proxy logs, referer headers, or network capture) sends the exact same query string again, the signature will still validate because the HMAC digest is a pure function of the static query params and the (unrotated) app secret — it carries no expiry semantics enforced by this code.

This is directly analogous to the Buffer `BufferRouter.unlock` finding: a timestamp is present and cryptographically bound into a signed/verified payload, but the relying code never validates that timestamp is close to "now," so an old, valid signed request can be reused/replayed to the app's benefit (or attacker's benefit) at a time the original request wasn't intended to be valid for.

### Impact Explanation
Because the app-proxy signature has no expiry check, any signed app-proxy URL that is ever observed by a third party (logs, browser history/back-forward cache, shared/cached links, HTTP referer leakage to third-party resources loaded by the proxied page) remains a valid, forgeable-looking authenticated request indefinitely. This allows an unrelated/anonymous actor to replay that exact request against the app-proxy endpoint at any later time, causing the endpoint to treat the replayed request as an authentic, freshly-issued Shopify app-proxy call. Depending on what the app-proxy action does (e.g., triggers a purchase-related action, mutates state, discloses shop-specific data), this constitutes an accepted forged/stale signed request being treated as legitimate.

### Likelihood Explanation
Exploitation only requires capturing one previously valid, signed app-proxy request URL — no secret key, no privileged access, and no additional cryptographic work is needed, since the signature itself is still cryptographically valid forever. This is reachable by any anonymous party who can obtain the URL through normal, unprivileged means (e.g., via Referer leakage, shared links, or logs), matching the "unprivileged path" requirement.

### Recommendation
Add an explicit timestamp freshness check in `query_string_valid?` (or `verify_proxy_request`) in `lib/shopify_app/controller_concerns/app_proxy_verification.rb`: reject the request if `query_hash["timestamp"]` is missing, non-numeric, or outside an acceptable tolerance window (e.g., ±5 minutes) of the current server time, in addition to the existing HMAC signature check.

### Proof of Concept
1. A merchant/customer triggers a legitimate app-proxy request; the resulting fully-signed URL (including `timestamp` and `signature`) is leaked via a `Referer` header, shared link, or server log.
2. At an arbitrary later time, an unrelated party replays the exact same URL (same `timestamp` and `signature`) to the app's `/app_proxy/...` endpoint.
3. `query_string_valid?` recomputes the HMAC over the same static params and it matches, so `verify_proxy_request` allows the request through — with no code path anywhere checking that `timestamp` is anywhere near the current time, per [3](#0-2) .

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
