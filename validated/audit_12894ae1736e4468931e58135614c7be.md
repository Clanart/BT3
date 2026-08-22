### Title
Missing Timestamp Freshness Check in App Proxy Signature Verification Allows Indefinite Replay of Captured Signed Requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates only that the HMAC signature matches the query parameters of an incoming App Proxy request. Although Shopify includes a `timestamp` parameter as part of the signed query string, the code never checks that timestamp against the current time, so a previously captured, validly-signed App Proxy request can be replayed at any point in the future and will still pass verification.

### Finding Description
The `AppProxyVerification` concern is meant to ensure that requests reaching an app's App Proxy controller genuinely originated from Shopify (i.e., were forwarded from a storefront proxy request signed with the app's shared secret) [1](#0-0) .

The actual verification logic strips the `signature` parameter, recomputes an HMAC-SHA256 over the remaining sorted query parameters (which includes `shop`, `path_prefix`, and `timestamp`), and compares it to the provided signature using a constant-time comparison [2](#0-1) :

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

The `timestamp` value is present in the query string and is part of what gets signed, but nowhere in `query_string_valid?` or `verify_proxy_request` is `Time.now` (or any freshness threshold) compared against it. This is the same bug class described in the external Chainlink report: a source of trusted, timestamped data (`latestRoundData()` / here, a signed query string with a `timestamp` field) is consumed without validating that the timestamp is recent, so stale/replayed data is treated as equally valid to fresh data.

The test suite confirms this behavior explicitly — a request with a fixed historical `timestamp=1466106083` (an epoch value years in the past) is asserted to pass verification and return `200 OK` [3](#0-2) .

### Impact Explanation
Any request forwarded through the App Proxy path is publicly reachable (App Proxy URLs are exposed on the shop's storefront domain, reachable by anonymous visitors/attackers who can observe network traffic, logs, referrers, browser history, or a shared/cached URL). If an attacker captures one legitimately signed App Proxy request (e.g., via browser history, a shared link, HTTP referer leakage, or a monitoring/logging system), that exact request can be replayed against the app indefinitely — the signature will remain valid forever since there is no expiry enforcement. Depending on what the App Proxy controller action does (e.g., state-changing actions keyed off query parameters, or actions that trust `shop`/other parameters as authenticated context), this enables a forged/replayed signed request to be accepted long after it should have expired, potentially enabling repeat state changes or trust decisions based on stale, attacker-replayed proxy calls.

### Likelihood Explanation
Exploitation requires the attacker to have captured a previously valid, signed App Proxy query string (e.g., via URL/log leakage, browser history, referer headers, or shared links), which is a realistic occurrence for GET-based proxy requests. Once captured, the replay itself requires no secret knowledge and can be repeated an unlimited number of times with no rate or time restriction from this concern.

### Recommendation
Add a staleness check on the `timestamp` parameter in `query_string_valid?` (or `verify_proxy_request`) similar to Shopify's own guidance for other signed callback flows: compare `Time.now.to_i - timestamp.to_i` against a bounded threshold (e.g., a few minutes) and reject the request if it exceeds that threshold, in addition to the existing signature check.

### Proof of Concept
1. A legitimate App Proxy request is issued to `/app_proxy/basic?shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-signature-for-these-params>`.
2. An attacker who obtains this exact URL (from browser history, logs, a shared bookmark, etc.) at any later date (days, months, or years later) replays the identical request.
3. `query_string_valid?` recomputes the HMAC over the same parameters (including the old `timestamp`) and finds it matches, returning `true`; `verify_proxy_request` therefore allows the request through to the controller action, exactly as demonstrated by the existing test using a hardcoded historical timestamp still returning `assert_response :ok` [3](#0-2) .

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L1-14)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module AppProxyVerification
    extend ActiveSupport::Concern
    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_proxy_request
    end

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
