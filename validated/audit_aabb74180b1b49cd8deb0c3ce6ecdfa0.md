### Title
Missing recency (timestamp freshness) check in App Proxy signature verification allows indefinite replay of a captured signed request - (File: `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` verifies that the `signature` query parameter matches an HMAC computed over the other query parameters (which include a `timestamp` value), but never checks that the `timestamp` is actually recent. This is the same bug class as the reported Chainlink issue: a value that is designed to convey "when this data/signature was produced" (`updatedAt` in the oracle report, `timestamp` here) is present in the payload but is never validated for staleness before the signed data is trusted and acted upon.

### Finding Description
`query_string_valid?` computes the expected signature over the full query hash (including `timestamp`) and compares it to the supplied `signature`: [1](#0-0) 

Nowhere in `verify_proxy_request` or `query_string_valid?` is `timestamp` compared against `Time.now` (or any tolerance window) to reject old requests. As long as `signature` matches the HMAC of the query string, the request is accepted regardless of how old `timestamp` is. This mirrors the reported oracle bug exactly: `getPriceFromChainlink()` validates `answer`, `answeredInRound`, and non-zero `updateAt`, but never checks `block.timestamp - updateAt` against a tolerance threshold, so a stale-but-technically-valid round is accepted. Here, a stale-but-technically-valid signed proxy request is accepted the same way.

The same missing-recency pattern also affects webhook verification: `PayloadVerification#hmac_valid?` computes an HMAC over `request.raw_post` only, with no timestamp/nonce component checked for freshness at all, so any previously captured, validly-signed webhook body can be resubmitted indefinitely: [2](#0-1) [3](#0-2) 

The app proxy case is the stronger analog because the signed payload explicitly carries a `timestamp` field (making the intent to support freshness checking evident, just as `updatedAt` is available from Chainlink), yet it's discarded rather than validated.

### Impact Explanation
Any anonymous party who captures a single valid, signed app-proxy request URL (via browser history, shared links, server logs, a referrer header leak, proxy/CDN logs, or network capture) can replay that exact URL against the app's `AppProxyVerification`-protected endpoint at any time in the future, and the request will be accepted as authentic on behalf of the originating shop. This is a "forged"-equivalent acceptance of a stale signed request outside its intended validity window — the endpoint has no way to distinguish a fresh proxy call from a replayed one. Depending on what the app-proxy-protected action does (e.g., mutating state, returning shop-scoped data), this enables state changes or data disclosure attributed to a shop without any fresh authorization from Shopify.

### Likelihood Explanation
Exploitation requires the attacker to first obtain one valid, signed app-proxy request (the signature/timestamp pair), which is a realistic bar since app-proxy URLs are storefront-facing and can leak through browser history, referrers, shared links, or logs. Once obtained, replay is trivial and requires no cryptographic secret and no interaction with Shopify or the merchant — a single HTTP request from an anonymous, unrelated party. This matches the "accepted forged/replayed signed request" acceptance criterion.

### Recommendation
- Add an explicit recency check in `AppProxyVerification#verify_proxy_request` / `query_string_valid?`: parse `timestamp` from the query hash and reject the request (return `false`/`head(:forbidden)`) if `Time.now.to_i - timestamp.to_i` exceeds a defined tolerance (e.g., a few minutes), consistent with the recommendation in the referenced report to bound staleness.
- Apply the analogous fix to webhook verification: leverage the `X-Shopify-Webhook-Id`/delivery metadata or enforce a maximum age on webhook processing to prevent indefinite replay of a captured, validly-HMAC'd webhook body.

### Proof of Concept
1. Attacker observes/captures a legitimate app-proxy request URL, e.g.:
   `GET /app_proxy/basic?shop=some-shop.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083&signature=<valid-hmac>`
2. Time passes (days/weeks) — the `timestamp` value is now stale.
3. Attacker (an unrelated, anonymous party) replays the exact same URL/query string against the app's proxy endpoint.
4. `query_string_valid?` in `app_proxy_verification.rb` recomputes the HMAC over the same query parameters (including the now-stale `timestamp`) and it still matches `signature`, since nothing enforces recency: [4](#0-3) 
5. The request is accepted as if it were a fresh call from Shopify's app-proxy layer on behalf of the shop, executing the controller action with no way for the app to detect the replay.

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

**File:** lib/shopify_app/controller_concerns/payload_verification.rb (L9-23)
```ruby
    def shopify_hmac
      request.headers["HTTP_X_SHOPIFY_HMAC_SHA256"]
    end

    def hmac_valid?(data)
      secrets = [ShopifyApp.configuration.secret, ShopifyApp.configuration.old_secret].reject(&:blank?)

      secrets.any? do |secret|
        digest = OpenSSL::Digest.new("sha256")
        ActiveSupport::SecurityUtils.secure_compare(
          shopify_hmac,
          Base64.strict_encode64(OpenSSL::HMAC.digest(digest, secret, data)),
        )
      end
    end
```

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L15-21)
```ruby
    def verify_request
      data = request.raw_post
      unless hmac_valid?(data)
        ShopifyApp::Logger.debug("Webhook verification failed - HMAC invalid")
        head(:unauthorized)
      end
    end
```
