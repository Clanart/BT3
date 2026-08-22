Confirmed: `AppProxyVerification#query_string_valid?` (`lib/shopify_app/controller_concerns/app_proxy_verification.rb:17-27`) and `WebhookVerification#verify_request` / `PayloadVerification#hmac_valid?` (`lib/shopify_app/controller_concerns/webhook_verification.rb:15-21`, `lib/shopify_app/controller_concerns/payload_verification.rb:9-23`) validate the HMAC signature against secrets but never check the `timestamp` parameter (app proxy) or any freshness/nonce indicator (webhooks) against a threshold, so a captured valid signed request can be replayed indefinitely — this is the same root-cause pattern as the oracle report ("no clear threshold to reject stale/old accepted data"), applied here to accepted signed requests rather than prices. The test at `test/shopify_app/controller_concerns/app_proxy_verification_test.rb:61-72` confirms an old fixed `timestamp=1466106083` (year 2016) is accepted as valid indefinitely as long as the signature matches.

### Title
Missing timestamp/staleness threshold in App Proxy and Webhook signature verification allows indefinite replay of captured signed requests - (File: lib/shopify_app/controller_concerns/app_proxy_verification.rb, lib/shopify_app/controller_concerns/webhook_verification.rb, lib/shopify_app/controller_concerns/payload_verification.rb)

### Summary
`AppProxyVerification#query_string_valid?` and `PayloadVerification#hmac_valid?` (used by `WebhookVerification`) only verify that the HMAC signature matches the request's parameters/body using the app secret. Neither enforces any threshold on the `timestamp` query parameter (app proxy) or any equivalent freshness check (webhooks), so a validly-signed request captured once (e.g. via network logs, browser history, referer leakage, or a compromised proxy) remains permanently acceptable to the app, with no expiry.

### Finding Description
In `app_proxy_verification.rb`, `verify_proxy_request` calls `query_string_valid?`, which recomputes the HMAC over all query params (including `timestamp`) and compares it to the provided `signature`: [1](#0-0) 
The `timestamp` value is only used as HMAC input, not compared to `Time.now` for staleness — there is no code path anywhere in this concern that rejects an old timestamp.

Similarly, `webhook_verification.rb` and `payload_verification.rb` verify HMAC over the raw body with no timestamp/nonce/replay check at all: [2](#0-1) [3](#0-2) 

This mirrors the oracle report's root cause: there is no explicit, enforced threshold determining when a signed artifact (price update / signed request) becomes stale and should be rejected, so an old, previously-valid signed value continues to be accepted as if it were current.

### Impact Explanation
An attacker who obtains a single valid signed App Proxy URL or webhook payload (e.g. leaked via logs, browser history, a misconfigured proxy, or a man-in-the-middle at any point in time) can replay it against the app's endpoint at any point in the future, and the app will treat it as an authentic, current Shopify-originated request. Depending on what the app does with `shop`/`path_prefix` params or webhook payload (e.g. state-changing actions, data updates), this could result in acceptance of a forged/stale signed request causing unwanted state changes.

### Likelihood Explanation
Exploitation requires the attacker to first obtain one legitimately signed request/URL (this is not a fully unauthenticated bypass of the shared secret), but once obtained, no additional Shopify interaction or expiry window limits reuse — likelihood is bounded by how signed URLs/payloads could leak (proxy logs, referer headers, browser history, shared secret rotation reliance on `old_secret` window in `payload_verification.rb`).

### Recommendation
Enforce a maximum allowed age for the `timestamp` parameter in `AppProxyVerification#query_string_valid?` (reject if `Time.now.to_i - timestamp.to_i` exceeds a defined threshold, e.g. a few minutes), and add an equivalent freshness/replay-window check for webhook requests (Shopify webhooks do not include a timestamp header by default in this repo's verification path, so consider tracking delivered webhook IDs to prevent replays, or documenting/enforcing a delivery-time window if available).

### Proof of Concept [4](#0-3) 
This existing test demonstrates that a request signed with `timestamp: "1466106083"` (June 2016) is still accepted as valid (`assert_response :ok`) with no staleness rejection — proving the same request/signature pair remains permanently replayable.

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
