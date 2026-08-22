### Title
Replayable App Proxy signed requests due to missing timestamp/nonce freshness check - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` only checks that the HMAC `signature` query parameter matches a value recomputed from the app secret and the remaining query parameters. It never checks that the `timestamp` parameter is recent or that the signed query string has not already been consumed, so any previously valid, signature-bearing request can be captured once and replayed an unlimited number of times.

### Finding Description
The verification logic is: [1](#0-0) 

`verify_proxy_request` calls `query_string_valid?`, which deletes `signature` from the query hash, recomputes an HMAC-SHA256 over the remaining sorted parameters (including `timestamp`), and compares it to the provided signature with `secure_compare`. There is no check anywhere in this module that `timestamp` is within an acceptable time window, and no per-request nonce/hash tracking to prevent the exact same query string (with the exact same signature) from being accepted again: [2](#0-1) 

This is directly analogous to the reported paymaster issue: a validator accepts a previously-issued cryptographic authorization (an HMAC signature over a fixed payload) without any state tracking to prevent reuse, so the same valid authorization can be resubmitted indefinitely. In the paymaster case this drained ETH deposits; here it means a signed app-proxy request — which Shopify only ever intended to be used once, at the time it forwarded a storefront visitor's request — remains permanently valid.

### Impact Explanation
Any controller that includes `ShopifyApp::AppProxyVerification` (e.g., the generated `AppProxyController` at `lib/generators/shopify_app/app_proxy_controller/templates/app_proxy_controller.rb`, or any app-defined controller such as the documented `ReviewsController` example) trusts `verify_proxy_request` as its sole authentication gate before executing the action. Because the signature check has no freshness or single-use enforcement, an unauthenticated party who has ever observed one valid app-proxy URL (e.g., via browser devtools/network logs, browser history, shared/cached links, referrer headers, or a proxy/log leak) can replay that exact URL directly against the app's public endpoint at any time in the future, bypassing Shopify entirely. If the underlying app-proxy action performs any state-changing or resource-consuming operation (e.g., redeeming a discount, submitting a review, incrementing a counter, triggering a paid API call), the replay allows repeated unauthorized execution of that action as if it were freshly authorized by Shopify — a concrete instance of "accepted forged/replayed signed request."

### Likelihood Explanation
Exploitation requires only observation of one legitimate app-proxy request URL, which is routinely visible to any browser user (network tab) and often preserved in browser history, shared links, or logs, since app proxy requests are simple GET/POST requests with the signing parameters exposed in the URL. No special privilege, merchant relationship, or secret knowledge is needed — this is reachable from a fully anonymous HTTP request, matching the required "unrelated-merchant/anonymous request" threat model.

### Recommendation
Enforce freshness on the `timestamp` parameter in `query_string_valid?` (reject requests whose timestamp is outside a small allowed window, e.g., a few minutes), and/or track consumed `(timestamp, signature)` pairs to prevent exact replay, mirroring the mitigation described in the referenced report (marking a request hash as used after first acceptance).

### Proof of Concept
1. A storefront visitor triggers an app-proxy request, e.g. `GET /apps/my-app/reviews?shop=some-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid_hmac>`.
2. The visitor captures this full URL (browser devtools, history, or logs).
3. At any later time, the visitor (now unauthenticated/anonymous, without going through Shopify's proxy) sends the identical URL directly to the app's public endpoint.
4. `AppProxyVerification#query_string_valid?` recomputes the HMAC over the same parameters and it matches, because there is no timestamp expiry or replay tracking: [1](#0-0) 
5. The controller action executes again, performing the same state-changing effect an unlimited number of times.

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
