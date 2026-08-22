### Title
App Proxy signature verification never checks the `timestamp` parameter for freshness, enabling replay of previously valid signed requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates that the HMAC `signature` query parameter matches a recomputed HMAC over the query string, but it never checks whether the `timestamp` value embedded in that same signed query string is recent. Any previously valid, fully-signed app-proxy URL (which necessarily contains a legitimately-signed `timestamp`) remains accepted forever, exactly analogous to the reported oracle bug where a signed price feed is accepted regardless of how old its publish time is.

### Finding Description
The verification concern only checks the cryptographic validity of the signature, not the age of the data it signs: [1](#0-0) 

`query_string_valid?` parses the query string, extracts `signature`, and recomputes the HMAC over the remaining (sorted) parameters — which includes `timestamp` — using `ActiveSupport::SecurityUtils.secure_compare`: [2](#0-1) 

Nothing in this method (or the calling `before_action :verify_proxy_request`) parses `timestamp` and rejects the request if it is older than some allowed window. This mirrors the report's root cause exactly: the presence of a valid signature is treated as proof of freshness, when in fact the signed payload (query string, including its `timestamp` field) can be arbitrarily old and still pass verification — just as `set_underlying_px` accepted an oracle feed's price without checking its publish time. The included tests confirm only signature correctness is exercised, never staleness: [3](#0-2) 

### Impact Explanation
If a previously-generated, fully signed app-proxy URL (or its query string) is ever exposed — via browser history, HTTP `Referer` headers, shared links, server/proxy access logs, or a man-in-the-middle capture over an unexpected channel — it remains valid and acceptable to the app indefinitely, since `query_string_valid?` has no expiry check. An attacker who obtains such a URL can replay it later to reach app-proxy protected endpoints under the shop's identity as of the (arbitrarily old) `timestamp`, running any logic app authors expose behind the app proxy (rendering data, or, if the app performs stateful side effects tied to the proxied request, unauthorized shop-context actions). This is analogous to the reported issue's "trade may be computed from obsolete data" — here, the app processes an obsolete/stale signed request as if it were current.

### Likelihood Explanation
Exploitation requires the attacker to have obtained a previously valid signed URL through some leakage channel (logs, referrer, browser cache/history, etc.) — Shopify does not expose signing secrets to the storefront, so this is not a trivial "anyone can forge" bug, but rather a replay-of-leaked-signed-request issue. Given app proxy URLs are appended to storefront pages and can leak via `Referer` headers to third-party resources loaded on the same page, or via shared/logged URLs, this leakage vector is realistic in production deployments, and the exploit itself (simply re-sending the exact same query string) has essentially no cost once obtained. There is no offsetting freshness or nonce-replay control, unlike the OAuth `state`/CSRF-token or session-token `exp` claim checks used elsewhere in this codebase.

### Recommendation
Enforce a maximum allowed age for the `timestamp` parameter in `query_string_valid?` (e.g., reject if `Time.now.to_i - timestamp.to_i` exceeds a small threshold, such as a few minutes), similar to how `LoginProtection`/`TokenExchange` already check session/token expiry (`current_shopify_session.expired?`, JWT `exp`). This closes the replay window even if a signed query string is later leaked.

### Proof of Concept
1. Capture a legitimately Shopify-signed app-proxy request URL for a shop (e.g., via a leaked `Referer` header or access log entry), including its `shop`, `path_prefix`, `timestamp`, and `signature` query parameters.
2. At any later time (hours, days, or longer), replay the exact same query string to the app's app-proxy endpoint.
3. `AppProxyVerification#query_string_valid?` recomputes the same HMAC over the same parameters and it matches, so `verify_proxy_request` allows the request through — as shown by the passing case in `app_proxy_verification_test.rb`, no time-based rejection ever occurs: [3](#0-2) 
4. The app-proxy protected controller action executes as if the request were fresh, despite the underlying `timestamp` being arbitrarily stale.

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
