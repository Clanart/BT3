### Title
Missing Timestamp Freshness Check in App Proxy Signature Verification Allows Indefinite Replay of Captured Signed Requests - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
The Sherlock finding is a "lack of sanity check" bug class: a numeric/time-bound input (`stopTime`) is accepted without validating it against the current time, producing an unintended, unrecoverable state. The analogous gap in `shopify_app` is in `ShopifyApp::AppProxyVerification`, which verifies the HMAC `signature` of an App Proxy request but performs no sanity check on the `timestamp` query parameter that Shopify includes specifically to bound the validity window of the signed request.

### Finding Description
`ShopifyApp::AppProxyVerification#verify_proxy_request` calls `query_string_valid?`, which recomputes the HMAC over all query parameters (including `timestamp`) and compares it to the supplied `signature`: [1](#0-0) 

The `timestamp` field is only used as HMAC input data — its value is never compared against the current server time to reject stale requests: [2](#0-1) 

Because there is no upper bound check (e.g. "reject if `Time.now.to_i - timestamp.to_i > threshold`"), a signed App Proxy URL remains valid forever, exactly analogous to the missing `stopTime > block.timestamp` sanity check in the reported bug: a time-bound parameter is embedded in the signed payload but is never validated as being "still in a valid window" at verification time.

### Impact Explanation
Any App Proxy URL that becomes exposed — via browser history, `Referer` headers leaking to third-party resources loaded from the proxied page, server logs, monitoring tools, or shared links — remains a permanently valid, replayable authenticated request. An unrelated/anonymous party who obtains a previously valid signed proxy URL (which necessarily carries the requesting `shop` value) can replay it indefinitely to invoke the app's proxy endpoint as if it were a fresh, legitimate request from Shopify, without needing to compromise the app's `secret`. This is a request-replay/spoofing issue for the app-proxy HMAC verification pathway explicitly called out as in-scope.

### Likelihood Explanation
Exploitability requires only that an attacker previously captured a valid signed proxy query string for some shop (a passive capture, not privileged access), then reissues that exact query string. Since `AppProxyVerification` is the documented and generator-provided mechanism (`app_proxy_controller.rb` template includes it), any app relying on it inherits the gap by default.

### Recommendation
Add an explicit timestamp sanity check before validating the signature, rejecting any request where `timestamp` is missing, malformed, or outside an acceptable window (e.g., a few minutes) relative to `Time.now.to_i`, mirroring how the audited contract was fixed by adding a `stopTime <= block.timestamp` check:
```ruby
def query_string_valid?(query_string)
  query_hash = Rack::Utils.parse_query(query_string)
  timestamp = query_hash["timestamp"]
  return false if timestamp.blank? || (Time.now.to_i - timestamp.to_i).abs > MAX_ALLOWED_SKEW
  ...
end
```

### Proof of Concept
1. Capture a valid, previously-issued App Proxy request URL for `shop=some-store.myshopify.com` (e.g., from a proxy `<img>`/`<script>` request logged by an intermediary, from browser history, or from a monitoring/APM tool that records full request URLs).
2. At any later time, replay the exact same query string (including `timestamp` and `signature`) against the app's App Proxy endpoint.
3. `query_string_valid?` in `lib/shopify_app/controller_concerns/app_proxy_verification.rb` recomputes the same HMAC and returns `true`, because no check is made on how old `timestamp` is — the controller action executes as though it were a fresh, legitimate proxied request, confirmed by the existing test suite validating only signature correctness with no time-based assertions: [3](#0-2)

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

**File:** test/shopify_app/controller_concerns/app_proxy_verification_test.rb (L30-37)
```ruby
  test "basic_query_string" do
    assert query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp="\
      "1466106083&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd")
    assert_not query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp="\
      "1466106083&evil=1&signature=f5cd7233558b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd")
    assert_not query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-"\
      "app&timestamp=1466106083&evil=1&signature=wrongwrong8b1c50102a6f33c0b63ad1e1072a2fc126cb58d4500f75223cefcd")
  end
```
