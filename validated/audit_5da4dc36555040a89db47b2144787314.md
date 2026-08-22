### Title
App Proxy Request Verification Never Checks Signature/Timestamp Freshness, Enabling Indefinite Replay - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification` validates only that the HMAC `signature` on an app-proxy query string matches the app secret; it never checks that the accompanying `timestamp` parameter is recent. As a result, any app-proxy request URL that is ever observed by a third party (via browser history, logs, referer headers, shared devices, etc.) remains a perpetually valid, unforgeable request that can be replayed at any point in the future — mirroring the reported bridge issue where a relayer could submit indisputable evidence with no bound on how old or how far in the future the associated block reference could be.

### Finding Description
`query_string_valid?` strips the `signature` param, recomputes the HMAC over the remaining sorted params (which include `timestamp`), and compares it to the submitted signature: [1](#0-0) 

Nowhere in this module — or anywhere else in the concern — is the `timestamp` value compared against the current time or any freshness window. The test suite itself demonstrates this: a request signed years ago (`timestamp=1466106083`, i.e. from 2016) is asserted valid: [2](#0-1) 

This is structurally identical to the reported bridge flaw: the verifying party (the contract, or here, the Rails controller) trusts a signed payload that references a point in time (block number / `timestamp`) without ever validating that the reference is recent. Just as a relayer could submit stale, indisputable evidence from more than `SLASHABLE_PERIOD` blocks ago, an attacker who captures any historical app-proxy URL (e.g. via a proxy access log, shared browser, or a misconfigured analytics/referrer leak) can replay that exact request indefinitely — the HMAC will always validate because `timestamp` is treated as opaque signed data rather than as a freshness assertion.

By contrast, Shopify's own documentation recommends validating that the app-proxy `timestamp` is recent to mitigate exactly this kind of replay, but `shopify_app` performs no such check before invoking the wrapped controller action via `verify_proxy_request`: [3](#0-2) 

### Impact Explanation
Any controller that mixes in `ShopifyApp::AppProxyVerification` to gate storefront-facing app-proxy endpoints will accept a captured/leaked request indefinitely. Depending on what the proxy action does (place orders, mutate shop data, trigger discounts, etc.), this enables replay-based state changes or information disclosure long after the original request should have expired, with no way for the app to detect or reject the stale replay.

### Likelihood Explanation
Exploitability depends on an attacker obtaining a previously valid, legitimately signed app-proxy URL (e.g., through logs, shared network capture, browser history sync, or a leaked referrer) — this does not require compromising the app secret. Given how app-proxy URLs are typically GET requests embedded in storefront pages/links, they are plausibly exposed through normal operational channels (server logs, CDN logs, browser history), making this a realistic, unprivileged replay vector rather than a purely theoretical one.

### Recommendation
Add a freshness check for the `timestamp` parameter in `query_string_valid?` (e.g., reject if `Time.now.to_i - timestamp.to_i` exceeds a small tolerance window, such as 90 seconds), in addition to the existing HMAC comparison, so that a valid signature computed at time T cannot be replayed well after T has passed.

### Proof of Concept
1. Configure an app with `ShopifyApp::AppProxyVerification` on a proxy controller action.
2. Capture (or compute, using the shared secret, once) a valid signed app-proxy query string, e.g. `shop=test.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>`.
3. At any later time — hours, days, or years after — send the identical query string to the proxy endpoint.
4. `query_string_valid?` recomputes the same HMAC and returns `true` because `timestamp` freshness is never checked, and `verify_proxy_request` allows the action to execute, as confirmed by the existing test using a decade-old `timestamp`: [2](#0-1) .

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
