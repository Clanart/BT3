### Title
Missing Upper Bound on Signed Request Freshness Enables Indefinite Replay of App Proxy and Webhook Requests - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#verify_proxy_request` and `ShopifyApp::WebhookVerification#verify_request` validate only that the HMAC signature matches the request parameters/body. Neither module enforces any upper bound on the age of the `timestamp` parameter (app proxy) or on request freshness (webhooks), so a signature that was valid at the time it was issued remains valid and acceptable forever.

### Finding Description
In `query_string_valid?` (`lib/shopify_app/controller_concerns/app_proxy_verification.rb:17-27`), the code recomputes the HMAC over the sorted query parameters (including `timestamp`) and compares it to the supplied `signature` via `secure_compare`. There is no check that `timestamp` (or any freshness value) falls within a bounded, recent window before the signature is accepted: [1](#0-0) 

The gem's own test suite demonstrates this directly: a request carrying `timestamp: "1466106083"` (year 2016) is asserted to pass verification with `assert_response :ok`, proving there is no upper bound on how old a validly-signed app proxy request can be and still be accepted: [2](#0-1) 

Similarly, `WebhookVerification#verify_request` (`lib/shopify_app/controller_concerns/webhook_verification.rb:15-21`) only checks `hmac_valid?(data)` against the raw POST body — it never checks a timestamp header or any expiry bound, so a previously valid, captured webhook payload+HMAC pair remains acceptable indefinitely: [3](#0-2) 

This is the same root-cause pattern as the reported `vestPeriod` issue: a security-relevant time-bound value (here, the acceptable "freshness window" of a signed request) has no upper bound, so once an attacker captures one valid signed request (e.g., via network logs, browser history, referrer leakage, or a compromised intermediary), that forged/replayed request can be re-submitted at any point in the future — even years later — and will still be treated as authentic by the app.

### Impact Explanation
An attacker who has ever observed a single valid, signed app-proxy request or webhook payload can replay it against the app indefinitely, since there is no freshness/expiry check to reject stale signatures. Depending on what the app does with app-proxy or webhook payloads (e.g., trigger shop-side actions, mutate state, or interpret shop-domain-scoped webhook data), this allows an unbounded-duration replay of a previously captured signed request, which is explicitly listed as an accepted vulnerability class (accepted forged signed request).

### Likelihood Explanation
The likelihood depends on an attacker being able to capture one valid signed request at some point (e.g., via logs, proxies, or exposed URLs, since app proxy requests are query-string GETs that can appear in browser history/referrers/analytics). Given that captured request, replay succeeds with certainty because there is no code path anywhere in `AppProxyVerification` or `WebhookVerification` that rejects old timestamps.

### Recommendation
Introduce and enforce a reasonable upper bound on request freshness:
- In `AppProxyVerification#query_string_valid?`, after verifying the HMAC, parse `timestamp` and reject requests where `Time.now.to_i - timestamp.to_i` exceeds a small, configurable threshold (e.g., a few minutes).
- In `WebhookVerification`, similarly validate a timestamp/replay-window where available, or otherwise implement nonce/idempotency tracking to reject already-seen webhook deliveries beyond a bounded window.

### Proof of Concept
Using the existing app proxy verification test as a demonstration of the flaw: an app proxy request signed with `timestamp=1466106083` (2016) is still accepted as valid by `query_string_valid?` and results in `assert_response :ok`, with no code path rejecting the stale timestamp: [2](#0-1) <br> [4](#0-3)

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L1-27)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module WebhookVerification
    extend ActiveSupport::Concern
    include ShopifyApp::PayloadVerification

    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_request
    end

    private

    def verify_request
      data = request.raw_post
      unless hmac_valid?(data)
        ShopifyApp::Logger.debug("Webhook verification failed - HMAC invalid")
        head(:unauthorized)
      end
    end

    def shop_domain
      request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]
    end
  end
end
```
