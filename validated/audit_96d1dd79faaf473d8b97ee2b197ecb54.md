### Title
Missing timestamp/freshness validation in App Proxy signature verification allows indefinite replay of previously valid signed requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
The external report's root cause is that a price-feed update accepts attacker/feeder-controlled data with no check that the accompanying timestamp is fresh, so a delayed (stale) message is later accepted as if it were current. The same class of defect — an HMAC/signature check that validates *authenticity* but never validates *freshness* — exists in `ShopifyApp::AppProxyVerification#query_string_valid?`.

### Finding Description
`query_string_valid?` recomputes the HMAC over all query parameters (including `timestamp`) and compares it to the supplied `signature` using `ActiveSupport::SecurityUtils.secure_compare`, but it never reads or bounds-checks the `timestamp` value itself: [1](#0-0) 

Shopify signs app-proxy requests with a `timestamp` parameter specifically so that host applications can reject stale/replayed URLs, but this gem's implementation only folds `timestamp` into the HMAC input — it is treated as opaque signed data, not as a freshness bound. As soon as a signed proxy URL exists (e.g., it leaks via browser history, shared links, server/CDN access logs, the `Referer` header, or a public bookmark), it remains a fully "valid" signed request forever, because `verify_proxy_request` only calls `query_string_valid?` and never rejects requests whose `timestamp` is older than an acceptable window: [2](#0-1) 

This mirrors the oracle report's pattern exactly: the "signature"/attribute is validated for correctness, but the code path that should reject stale data (here, an aged `timestamp`) is entirely absent, so old signed requests are accepted as if freshly issued by Shopify's proxy.

### Impact Explanation
Any controller that includes `ShopifyApp::AppProxyVerification` (per the documented pattern in `docs/shopify_app/engine.md` and the generator template `lib/generators/shopify_app/app_proxy_controller/templates/app_proxy_controller.rb`) will accept an intercepted, previously-valid signed proxy URL indefinitely — there is no time-boxing at all. If an app's proxy actions perform state-changing operations (e.g., a review submission, single-use redemption, form action) keyed off proxy query params, an attacker who has captured a once-valid signed URL can replay it at will to re-trigger that action or access proxied content well beyond the intended request window, without the merchant or storefront customer's continued involvement. This is analogous to the anchor oracle bug: an "accepted forged/stale signed request" being processed as current/authentic.

### Likelihood Explanation
Exploitation only requires the attacker to obtain a copy of a legitimately-signed app-proxy URL (via logs, browser history, `Referer` leakage, or a shared link) — no secret key or privileged access is needed, satisfying the "unprivileged/unrelated actor" bar. The missing check is unconditional (applies to every request through this concern), so likelihood of the defect being present is certain; the likelihood of practical exploitation depends on how easily a given app's signed proxy URLs leak, which is a realistic and common scenario for GET-based proxy links.

### Recommendation
In `query_string_valid?` (or `verify_proxy_request`), extract the `timestamp` query parameter and reject the request if `Time.now.to_i - timestamp.to_i` exceeds a small configurable tolerance (e.g., a few minutes), in addition to the existing HMAC comparison — mirroring the recommended fix in the referenced report (reject stale and future timestamps before trusting the payload).

### Proof of Concept
1. A merchant's storefront customer visits a signed app-proxy URL such as `/apps/my-app?shop=some-shop.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1700000000&signature=<valid-hmac>`.
2. This URL is captured by an attacker (e.g., via `Referer` header on an external site, shared in chat, or found in a log) well after the original request.
3. The attacker replays the exact same URL at any later time.
4. `verify_proxy_request` → `query_string_valid?` in `lib/shopify_app/controller_concerns/app_proxy_verification.rb` recomputes the HMAC over the same parameters (including the now-old `timestamp`) and it still matches, so `head(:forbidden)` is never called and the underlying proxy action executes as if it were a fresh, current request — with no code path ever comparing `timestamp` against the current time.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L6-13)
```ruby
    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_proxy_request
    end

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
