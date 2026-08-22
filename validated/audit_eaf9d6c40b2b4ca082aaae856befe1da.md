### Title
App Proxy request signatures are accepted indefinitely with no timestamp/freshness check - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb](https://github.com/Kirstentat/shopify_app--016/blob/main/lib/shopify_app/controller_concerns/app_proxy_verification.rb))

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates the `signature` query parameter over the full set of proxy query params (which includes a `timestamp` value), but it never checks that the `timestamp` is recent. Any previously-valid, signed app-proxy URL therefore remains permanently acceptable, mirroring the Chainlink report's root cause: a validation routine that only checks structural correctness of signed/oracle data but skips the check for staleness.

### Finding Description
`query_string_valid?` in `lib/shopify_app/controller_concerns/app_proxy_verification.rb` extracts the `signature` parameter, recomputes an HMAC over the remaining sorted query parameters, and compares it via `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

The `timestamp` parameter is included in the signature computation (as shown by the test fixtures using `timestamp=1466106083`, a fixed epoch value from 2016), but at no point is `Time.at(timestamp)` compared against the current time to enforce a validity window: [2](#0-1) 

This is the same class of bug as the Chainlink report: the verification function ("`ethPerCvx`" in the analog, "`query_string_valid?`" here) validates the cryptographic/structural correctness of the input but omits the freshness/staleness check ("`block.timestamp - cl.updatedAt <= 25 hours`" in the analog; here, no equivalent check exists at all for the proxy `timestamp`).

### Impact Explanation
Because the signature never expires, any app-proxy URL that was ever validly signed by Shopify (and subsequently observed by an unrelated party — e.g. via browser history, shared links, HTTP `Referer` headers, proxy/CDN logs, or a leaked bookmark) can be replayed by an anonymous, unrelated requester at any point in the future and will still pass verification. If the app's proxy endpoint performs any state-changing or data-disclosing action keyed off those query parameters (e.g. `shop`, `path_prefix`, or the app's own custom params reflected into the signed set), an attacker can trigger that action indefinitely after the fact, effectively replaying stale-but-"valid" signed requests — the direct analog of using a stale-but-technically-well-formed Chainlink response.

### Likelihood Explanation
Likelihood depends on how easily a valid signed app-proxy URL leaks and whether the app's proxy action is state-changing/sensitive; app-proxy URLs are storefront-facing and can leak via `Referer` headers, shared links, or logs, so exposure is plausible for any real-world storefront traffic. The verification code itself provides no mitigation regardless of the app's endpoint behavior, so the underlying weakness is unconditionally present wherever `ShopifyApp::AppProxyVerification` is used.

### Recommendation
Add an explicit freshness check on the `timestamp` query parameter in `query_string_valid?`, rejecting requests whose `timestamp` is older than a bounded window (e.g. a few minutes), similar to how the Chainlink fix required checking `block.timestamp - cl.updatedAt <= 25 hours`:
```ruby
def query_string_valid?(query_string)
  query_hash = Rack::Utils.parse_query(query_string)

  signature = query_hash.delete("signature")
  return false if signature.nil?

  timestamp = query_hash["timestamp"]
  return false if timestamp.nil? || (Time.now.to_i - timestamp.to_i).abs > MAX_ALLOWED_SKEW

  ActiveSupport::SecurityUtils.secure_compare(
    calculated_signature(query_hash),
    signature,
  )
end
```

### Proof of Concept
1. A merchant's storefront renders an app-proxy link such as `/apps/my-app?shop=store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>`.
2. This URL is captured by an unrelated/anonymous party (e.g., via a `Referer` header forwarded to a third-party analytics/ad script, a shared screenshot, or log access).
3. Regardless of how much time has passed, replaying the exact same URL against `GET /apps/my-app` still passes `query_string_valid?` because `calculated_signature` only recomputes the HMAC over the same params — there is no check that `timestamp` falls within an acceptable recent window, as confirmed in [3](#0-2) .
4. Any proxy controller action gated solely by `verify_proxy_request` therefore executes for the replaying anonymous party as if it were a fresh, legitimate Shopify-forwarded proxy request.

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
