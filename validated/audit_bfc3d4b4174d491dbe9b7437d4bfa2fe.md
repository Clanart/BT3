### Title
Webhook and App Proxy HMAC verification lack timestamp/replay checks, permitting indefinite replay of captured signed requests - (File: `lib/shopify_app/controller_concerns/payload_verification.rb`, `lib/shopify_app/controller_concerns/app_proxy_verification.rb`)

### Summary
The `PayloadVerification` and `AppProxyVerification` concerns validate an incoming request purely by recomputing an HMAC over the request body/query and comparing it to the supplied signature. Neither concern checks the age of the request (no timestamp/nonce freshness check), so a signature that was valid at capture time remains valid forever. This mirrors the report's underlying bug class: trusting externally supplied signed/attested data without validating its freshness, allowing stale (here, replayed) data to be accepted as current.

### Finding Description
`hmac_valid?` in `PayloadVerification` only recomputes the HMAC-SHA256 digest of the raw POST body against `ShopifyApp.configuration.secret`/`old_secret` and does a constant-time comparison — there is no check of a timestamp or nonce to bound how old the signed payload may be: [1](#0-0) 

This is used directly by `WebhookVerification#verify_request`, which is the entire authorization mechanism for the unauthenticated `WebhooksController#receive` endpoint: [2](#0-1) [3](#0-2) 

Similarly, `AppProxyVerification#query_string_valid?` recomputes an HMAC-SHA256 over the full sorted query string (which does include a `timestamp` parameter as part of the signed data, per Shopify's app-proxy signing scheme) but the code never reads or validates that timestamp against current time — it only checks that the signature matches: [4](#0-3) 

Because the `timestamp` value is merely folded into the signed digest rather than independently verified for recency, any request (webhook delivery or app-proxy request) captured by an unrelated observer — e.g., via browser history, proxy/CDN logs, a misconfigured logging pipeline, or a passive network observer before TLS termination — can be replayed byte-for-byte at any later time and will still pass verification, since the code performs no `Time.now - timestamp <= threshold` style check anywhere in these two concerns.

### Impact Explanation
An attacker with a previously captured valid webhook or app-proxy request can replay it indefinitely against these unauthenticated endpoints. For webhooks, this can cause repeated re-processing of stale business events (e.g., re-triggering `AppUninstalledJob`-style side effects, mandatory privacy jobs, or app-specific job logic keyed off webhook payloads) long after the underlying Shopify shop state has changed. For app-proxy endpoints, a captured signed request can be replayed to re-invoke proxy actions as if freshly initiated by the shop's storefront, without going through Shopify's proxy request/response cycle again. This is the same class of impact the ChainLink report describes — the system trusts old data as if it were current because no freshness bound is enforced.

### Likelihood Explanation
Likelihood is moderate: it requires an attacker to first obtain a legitimately signed webhook/app-proxy request (via logging systems, browser history, referer leakage, or a network vantage point), which is a real but non-trivial precondition. Once obtained, replay is trivial and unbounded in time because there is no expiry enforcement in the gem's verification code itself.

### Recommendation
Add a freshness check alongside the HMAC comparison in both `PayloadVerification`/`WebhookVerification` and `AppProxyVerification`:
- For app-proxy requests, parse the `timestamp` query parameter and reject the request if `Time.now.to_i - timestamp.to_i` exceeds a small threshold (e.g., a few minutes), in addition to the existing signature check.
- For webhooks, validate the `X-Shopify-Webhook-Id`/delivery timestamp header (or track processed webhook IDs) to reject requests outside an acceptable time window or already-processed IDs, preventing indefinite replay.

### Proof of Concept
1. Capture a legitimately signed app-proxy request URL (e.g., from browser history, a shared log, or a proxy) containing `signature=...&timestamp=...`.
2. At any later time, replay the exact same query string to the app's proxy endpoint protected by `ShopifyApp::AppProxyVerification`.
3. `query_string_valid?` in `lib/shopify_app/controller_concerns/app_proxy_verification.rb` recomputes the same HMAC and returns `true` regardless of how old `timestamp` is, so the request is processed as if newly issued.
4. The same applies to a captured webhook POST body/headers replayed against `ShopifyApp::WebhooksController#receive` — `PayloadVerification#hmac_valid?` accepts it unconditionally as long as the HMAC matches, with no check on delivery recency.

### Citations

**File:** lib/shopify_app/controller_concerns/payload_verification.rb (L13-23)
```ruby
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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L14-21)
```ruby

    def verify_request
      data = request.raw_post
      unless hmac_valid?(data)
        ShopifyApp::Logger.debug("Webhook verification failed - HMAC invalid")
        head(:unauthorized)
      end
    end
```

**File:** app/controllers/shopify_app/webhooks_controller.rb (L7-14)
```ruby
    def receive
      params.permit!

      ShopifyAPI::Webhooks::Registry.process(
        ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h),
      )
      head(:ok)
    end
```

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
