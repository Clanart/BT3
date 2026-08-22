### Title
App Proxy signed requests lack replay protection (no timestamp freshness check) - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification` validates that an incoming app-proxy request's `signature` query parameter matches an HMAC computed over the remaining query parameters, but it never checks that the `timestamp` parameter is recent. As a result, any previously valid, signed app-proxy URL remains permanently replayable.

### Finding Description
`query_string_valid?` in `lib/shopify_app/controller_concerns/app_proxy_verification.rb` extracts the `signature` parameter and recomputes an HMAC-SHA256 over the sorted remaining query parameters (which include `shop`, `path_prefix`, `timestamp`, and any Shopify-supplied context such as a logged-in customer/session identifier) using the app's shared secret, then does a constant-time comparison against the provided signature: [1](#0-0) 

`verify_proxy_request` only rejects requests when the signature doesn't match — it performs no check on how old `timestamp` is: [2](#0-1) 

Since app-proxy requests are sent as GET requests with all authenticating data embedded in the URL query string, and the signature is a deterministic function of those same query parameters (with no expiry/nonce), any URL that is captured once (e.g. via browser history, proxy/CDN logs, `Referer` headers leaking to third-party resources loaded by the proxied page, or a shared/public computer) remains a validly "signed" request forever. There is no mechanism in this codebase — analogous to the missing nonce in the reported `StructHash.Order` — to bind the signature to a single use or a bounded time window.

This mirrors the reported bug class: a party holding a previously-issued signed payload can re-submit it indefinitely and have it accepted as authentic, because the verification only checks *that* something was signed, not *when* it was signed or whether it has already been consumed.

### Impact Explanation
An attacker who obtains a single leaked app-proxy URL (no privileged role required — this is reachable by anyone who can capture that URL) can replay it at any time in the future and have the request accepted by the app as an authentic, Shopify-originated app-proxy call for that shop/context. If the app relies on any identity-bearing query parameter forwarded by Shopify through the proxy (e.g., a customer or session identifier) for authorization decisions, this enables indefinite cross-user/cross-session impersonation via an accepted forged (replayed) signed request. Severity depends on what the specific app does with the verified parameters, but the primitive itself — permanent replayability of a "verified" signed request — is present in the gem's shared verification logic used by every app-proxy controller that includes this concern.

### Likelihood Explanation
Exploitation requires only that an attacker obtain one previously valid signed URL (no operator/admin privilege, no secret compromise required) — a much weaker requirement than the referenced report's `OPERATOR_ROLE` precondition. App-proxy URLs are GET requests, which are commonly logged by CDNs, proxies, and can leak via `Referer` headers when the proxied Liquid page embeds external resources, making capture realistic in production.

### Recommendation
Add a timestamp-freshness (and/or nonce/single-use) check in `verify_proxy_request`/`query_string_valid?`: reject requests whose `timestamp` is older than a short allowed skew window, and consider tracking recently-used signatures to prevent immediate replay, similar to the recommended mitigation of introducing a nonce and binding verification to a single intended use in the original report.

### Proof of Concept
1. A merchant's storefront page is loaded through the app's App Proxy endpoint (e.g., `/apps/my-app/...?shop=...&path_prefix=...&timestamp=...&signature=...`), and the resulting URL is captured by any means (browser history, shared computer, logged `Referer`, or a proxy/CDN access log).
2. At any later time, an unrelated attacker replays the exact same URL (including its `signature`) against the app.
3. `AppProxyVerification#verify_proxy_request` recomputes the HMAC over the same query parameters and it matches, so the request passes verification via `ActiveSupport::SecurityUtils.secure_compare` in `calculated_signature`: [3](#0-2) 
4. The controller action executes as if the request were a fresh, legitimate call from Shopify, with no check that the `timestamp` has expired — confirming indefinite replayability.

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
