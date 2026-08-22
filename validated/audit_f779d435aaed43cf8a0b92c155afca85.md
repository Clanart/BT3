### Title
Missing deadline/staleness check on App Proxy signed requests allows indefinite replay of captured signatures - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates an app-proxy request purely by recomputing an HMAC over the query string (which includes a `timestamp` parameter) and comparing it to the supplied `signature`. The `timestamp` value is treated as opaque signed data, not as an expiry/deadline check: there is no code path anywhere in the gem that rejects a request because its `timestamp` is old. This is the same bug class as the reported `registerAgentsWithSignature` issue — a signed message that includes time-related data but never enforces that the signature/request is still within a valid window, so a previously valid signed request can be replayed indefinitely.

### Finding Description
`verify_proxy_request` is the sole gate protecting any controller that includes `AppProxyVerification`: [1](#0-0) 

`query_string_valid?` extracts the `signature` param, then recomputes an HMAC-SHA256 over all remaining query params (sorted) using the app secret, and does a constant-time compare. `timestamp` is just one of the "remaining query params" folded into `calculated_signature` — it is never independently parsed, compared to `Time.now`, or rejected if too old: [2](#0-1) 

The existing test suite explicitly confirms this: a request whose `timestamp=1466106083` (year 2016) is accepted as valid indefinitely, as long as the signature matches: [3](#0-2) 

A repo-wide search confirms no expiry/staleness logic exists anywhere for app-proxy timestamps — the only "timestamp" and "expire" hits in `lib/` relate to unrelated things (OAuth session/access-token expiry columns, JWT `exp`/`jwt_expire_at` for session tokens), none of which apply to the App Proxy signature path.

This mirrors the reported bug class exactly: a signed payload includes what looks like a temporal binding (`timestamp`), but the verifying code never enforces a deadline, so any captured/leaked valid signed query string (e.g., from browser history, proxy/CDN logs, referrer headers, shared links, or network capture on the storefront-to-app leg) remains a fully valid, replayable authenticated request forever.

### Impact Explanation
Any party who captures one valid App Proxy request URL (which is a plain, unencrypted GET query string, easily exposed via logs, referrer headers, browser history, or shared links) can replay it against the app's App Proxy endpoint at any point in the future, since the "authorization" is a static signature with no time-bound enforcement. This lets an attacker impersonate a legitimate storefront-proxied request indefinitely, invoking business logic in any controller that trusts `ShopifyApp::AppProxyVerification` (e.g., `AppProxyController` and any custom controllers under the app-proxy namespace) as if it came fresh from Shopify.

### Likelihood Explanation
Moderate-to-high: App Proxy URLs are transmitted over plain query strings and commonly logged by web servers, CDNs, and analytics tools, or exposed via `Referer` headers to third-party resources loaded on the storefront page. Because there is no expiry, a single leaked URL is a permanent bypass, not one requiring a live window — unlike a normal replay attack that would need to be exploited within seconds/minutes if a deadline existed.

### Recommendation
Add explicit timestamp validation in `query_string_valid?`/`verify_proxy_request`: parse the `timestamp` query parameter and reject the request (return `false` / `head(:forbidden)`) if `Time.now.to_i - timestamp.to_i` exceeds a reasonably small allowed skew (e.g., a few minutes), in addition to the existing HMAC check.

### Proof of Concept
1. A storefront page renders the App Proxy URL (e.g. `https://shop.myshopify.com/apps/my-app?shop=...&path_prefix=...&timestamp=1466106083&signature=...`) as a link, or it's requested via `<img>`/`<script>`/analytics beacon, or captured from server access logs / CDN logs / browser history.
2. Attacker records this exact URL.
3. Regardless of how much time has passed (as shown in the test using `timestamp=1466106083`, a value from 2016, still validating successfully), the attacker replays the identical URL directly to the app.
4. `query_string_valid?` recomputes the same HMAC over the same params and matches the same `signature`, so `verify_proxy_request` allows the request through, executing the controller action as if Shopify had just signed it. [3](#0-2)

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
