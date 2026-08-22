### Title
Missing timestamp freshness validation in App Proxy HMAC verification allows unlimited replay of captured proxy requests - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates only that the HMAC signature over the query string matches; it never checks that the `timestamp` parameter included in that signed query string is recent. As a result, any previously valid, signed App Proxy request remains permanently replayable by anyone who has ever observed it.

### Finding Description
The App Proxy verification concern extracts the `signature` parameter and recomputes the HMAC over the remaining sorted query parameters (which include `timestamp`, `shop`, `path_prefix`, and any app-specific params), comparing it with `ActiveSupport::SecurityUtils.secure_compare`: [1](#0-0) 

Nowhere in this module — or anywhere else in `verify_proxy_request` — is the `timestamp` value compared against the current time to enforce a freshness/expiry window: [2](#0-1) 

This mirrors the reported bug class: a signed data value (`timestamp`, analogous to the oracle record) is present and cryptographically bound into the signature, but the consuming code (`query_string_valid?`, analogous to `Staking.totalControlled()`) never validates that this value reflects a "fresh" state before trusting the request. The signature only proves the query string wasn't tampered with — it does not, by itself, prove recency, and `shopify_app` supplies no additional recency check.

This is directly confirmed by the existing test suite, where a timestamp from 2016 (`1466106083`) is accepted as valid indefinitely, with no notion of expiry: [3](#0-2) 

### Impact Explanation
Any request that flows through Shopify's App Proxy (i.e., a storefront URL under `/apps/<subpath>`) is signed once by Shopify at request time and forwarded to the app. Because there is no freshness check, an unrelated/anonymous party who captures a legitimately proxied request URL — via browser history, shared links, network logs, caching proxies/CDNs, search engine indexing, or a Referer leak from a storefront page — can replay that exact query string against the app's proxy endpoint at any point in the future, and `shopify_app` will treat it as a fully authentic, current App Proxy request. If the app performs any state-changing or shop/customer-context-sensitive action off App Proxy parameters (cart tokens, customer identifiers, logged_in_customer_id, etc.), this enables replay-based abuse using stale, no-longer-valid context, analogous to how a stale oracle record can be replayed to arbitrage `Staking.totalControlled()`.

### Likelihood Explanation
Exploitation requires only capturing one previously issued, validly signed App Proxy request URL — which is not a secret to the party who receives it (e.g., the shopper it was generated for, or anyone who can observe network traffic/logs/caches) — and replaying it with a normal unauthenticated HTTP request. No shop credentials, session tokens, or privileged access are needed to perform the replay itself.

### Recommendation
Enforce a bounded freshness window on the `timestamp` parameter in `query_string_valid?` (e.g., reject requests where `Time.now.to_i - timestamp.to_i` exceeds a small threshold, such as Shopify's documented tolerance), in addition to the existing HMAC signature check, before accepting the App Proxy request as valid.

### Proof of Concept
1. Capture any valid, previously-issued App Proxy request URL for a shop (e.g., from a browser's network tab, a shared link, or a cached page), including its `signature` and `timestamp` query parameters.
2. At any later time, anonymously replay the exact same query string to the app's App Proxy endpoint.
3. `AppProxyVerification#query_string_valid?` recomputes the HMAC and finds it matches (since the query string, including the old timestamp, is unchanged), so the request passes verification and reaches application logic — as demonstrated by the existing test accepting a decade-old timestamp: [4](#0-3)

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
