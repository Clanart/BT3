### Title
Missing timestamp freshness check in `AppProxyVerification` allows indefinite replay of valid signed App Proxy requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
The Bootstrap.vy report describes a class of bug where a time-bound invariant (`vote_end < lock_end`) is not enforced, letting an attacker reuse the same voting rights indefinitely by re-entering the system. The structural analog in `shopify_app` is `ShopifyApp::AppProxyVerification`, which validates the HMAC `signature` of an App Proxy request but never checks the `timestamp` query parameter for freshness/expiration. Any request that was ever validly signed by Shopify (and observed by an attacker, e.g. via browser network logs, proxies, logging, or referrer leakage) remains permanently valid and can be replayed unlimited times with no time-window enforcement.

### Finding Description
`ShopifyApp::AppProxyVerification#verify_proxy_request` calls `query_string_valid?`, which recomputes the HMAC over all query parameters except `signature` and compares it with `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

Although Shopify includes a `timestamp` query parameter in every App Proxy request specifically so that consuming apps can reject stale/replayed requests, this module treats `timestamp` as just another signed field to include in the HMAC computation — it never compares it against the current time or enforces any expiration window. This mirrors the Bootstrap.vy defect: a time-bound field is present (`timestamp` here, `deposit_end`/`vote_end` there) but the code never checks it against the "now" boundary that should invalidate it, so once a valid signed artifact exists, it can be reused indefinitely.

### Impact Explanation
Because the signature only proves that a query string was once produced by Shopify with the app's shared secret, and there is no expiry enforcement, any historically valid, captured App Proxy URL (e.g. `?shop=...&path_prefix=...&timestamp=...&signature=...`) is a permanently valid credential. An attacker who obtains one such URL (via browser history, shared links, proxy/CDN logs, XSS on the storefront, or a malicious partner) can replay it indefinitely to invoke the same app-proxy action as if from the storefront — including actions gated only by App Proxy verification (no separate session/token check). This can lead to repeated unauthorized triggering of app-proxy functionality tied to that shop/path, analogous to the reward-draining "double action" impact in the original report, scaled to whatever the app's proxy endpoint does (e.g., generate discounts, submit orders/reviews, redeem codes) each time it's replayed.

### Likelihood Explanation
Exploitability requires an attacker to have observed one legitimately signed App Proxy request. This is a realistic bar: App Proxy requests traverse browsers, CDNs/logs, referrers on storefront pages, and are not treated as secret by front-end code, so the requisite prerequisite (an unrelated party capturing a valid signed URL) is plausible. There is no code path in this repository that mitigates replay once a signature is known; the bug is systemic to every controller including `ShopifyApp::AppProxyVerification`, not app-specific misconfiguration.

### Recommendation
Enforce a freshness window inside `query_string_valid?`/`verify_proxy_request` by parsing `timestamp` from the query hash and rejecting requests where `Time.now.to_i - timestamp.to_i` exceeds a small tolerance (e.g. a few minutes), in addition to the existing HMAC check, before returning `true`.

### Proof of Concept
1. Attacker observes/captures one valid, Shopify-signed App Proxy URL previously sent to any endpoint mixing in `ShopifyApp::AppProxyVerification`, e.g.:
   `GET /app_proxy/basic?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>`
2. Attacker replays the exact same URL/query string at any later time (days, months, years later).
3. `query_string_valid?` recomputes the same HMAC over the same query hash (the `timestamp` value is unchanged, so the signature still matches) and returns `true`; `verify_proxy_request` allows the request through as if it were a fresh, legitimate Shopify-forwarded request. [1](#0-0)

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
