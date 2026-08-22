### Title
Webhook shop attribution spoofing via unsigned `X-Shopify-Shop-Domain` header allows cross-shop webhook injection - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` only HMAC-verifies the raw request body against the app's shared secret; it never verifies that the `X-Shopify-Shop-Domain` header actually corresponds to the shop that produced that body. Because the app secret is shared across every shop that has installed the app, any actor who can obtain one validly-signed webhook payload (e.g. by installing the app on their own store) can replay that payload while substituting an arbitrary shop domain in the unsigned header, causing the app to process attacker-controlled webhook data under a victim shop's identity.

### Finding Description
`ShopifyApp::PayloadVerification#hmac_valid?` computes `HMAC-SHA256(secret, request.raw_post)` and compares it (via `secure_compare`) to the `X-Shopify-Hmac-Sha256` header: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` only checks this HMAC over the raw body, and separately exposes `shop_domain` sourced directly and unverified from `HTTP_X_SHOPIFY_SHOP_DOMAIN`: [2](#0-1) 

`ShopifyApp::WebhooksController#receive` forwards the full raw headers (including the unsigned shop-domain header) into `ShopifyAPI::Webhooks::Registry.process` after only the body HMAC check passes: [3](#0-2) 

Generated job templates (both the declarative-webhook template and custom controller docs) then use this unverified `shop`/`shop_domain` value directly to look up and act on a specific shop's record, e.g. the privacy job template does `Shop.find_by(shopify_domain: shop_domain)` and runs `shop.with_shopify_session`: [4](#0-3) 

The webhook secret (`ShopifyApp.configuration.secret`) is a single app-wide value, not a per-shop secret — the same secret validates HMACs for every shop that has the app installed. Consequently, HMAC validity only proves "this body was HMAC'd with the app secret at some point by some install of this app" — it says nothing about which shop the header claims the event belongs to. This mirrors the root cause in the reported Merkle-distributor bug: a claim ("this belongs to shop/gauge X") is trusted without independently verifying it against the actual source of truth (the signed content), so an attacker can attach an unrelated/forged attribution to otherwise-valid signed data.

### Impact Explanation
An attacker who installs the app on their own (even free/dev) store is a legitimate holder of validly-HMAC'd webhook traffic under the shared app secret. By capturing one such legitimate webhook POST and replaying it against the app's webhook endpoint with the `X-Shopify-Shop-Domain` header changed to a victim shop's domain, the app will accept the HMAC (it only covers the body) and dispatch the corresponding job (`shop_domain: <victim>`, `webhook: <attacker-controlled body>`). Depending on which webhook topics/jobs the app has implemented, this enables cross-shop event injection — e.g., forcing data-redaction/data-request flows, order/product/cart update handlers, or any custom job logic to run against a victim shop record using attacker-chosen webhook body content. This is a cross-shop integrity/data-injection issue reachable by any actor able to install the app once (effectively unprivileged relative to any specific victim shop).

### Likelihood Explanation
Likelihood is medium-to-high: exploitation requires only (1) installing the target app on an attacker-controlled shop to obtain one legitimately signed webhook (trivial and free for most public apps), and (2) replaying the captured request with the header value swapped, which requires no cryptographic break — `X-Shopify-Shop-Domain` is not covered by the HMAC at all. No credentials, session tokens, or knowledge of any victim-shop secret are needed.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` (or any header) as shop attribution unless it is itself cryptographically bound to the request. Either:
- Include the shop domain in the HMAC-signed payload and validate it against the header/claimed value before dispatching jobs, or
- Verify that the resolved shop domain has an active, valid session/install record and cross-check plausibility of the webhook body against that shop (e.g., reject if the body's own shop-scoped identifiers don't match), or
- Compute per-shop HMAC verification using that shop's own credentials rather than a single shared app secret, if feasible with the API version in use.
At minimum, document this trust boundary prominently so downstream job implementers (like the generated privacy-job templates) don't assume `shop_domain` is verified.

### Proof of Concept
1. Attacker installs the target Shopify app on `attacker-shop.myshopify.com`, triggering a legitimate webhook (e.g. `orders/create`) with a valid `X-Shopify-Hmac-Sha256` computed over the JSON body using the app's shared secret, and header `X-Shopify-Shop-Domain: attacker-shop.myshopify.com`.
2. Attacker captures this full HTTP request (headers + body).
3. Attacker resends the identical request to the app's `/webhooks/:type` endpoint, changing only `X-Shopify-Shop-Domain` to `victim-shop.myshopify.com` (topic header can be kept or changed to any topic the app has configured a job for).
4. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` — since the body wasn't modified, the HMAC still matches the shared secret, so verification passes.
5. `WebhooksController#receive` forwards `headers` (still containing the spoofed `X-Shopify-Shop-Domain`) to `ShopifyAPI::Webhooks::Registry.process`, which invokes the configured job with `shop: "victim-shop.myshopify.com"` and the attacker's body.
6. The job (e.g., a generated `CustomersRedactJob` or any shop-scoped job) executes business logic against `victim-shop.myshopify.com`'s record using attacker-controlled webhook content, despite `victim-shop` never having sent this event.

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L13-26)
```ruby
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
```

**File:** app/controllers/shopify_app/webhooks_controller.rb (L1-16)
```ruby
# frozen_string_literal: true

module ShopifyApp
  class WebhooksController < ActionController::Base
    include ShopifyApp::WebhookVerification

    def receive
      params.permit!

      ShopifyAPI::Webhooks::Registry.process(
        ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h),
      )
      head(:ok)
    end
  end
end
```

**File:** lib/generators/shopify_app/add_privacy_jobs/templates/customers_redact_job.rb.tt (L1-20)
```text
class CustomersRedactJob < ActiveJob::Base
  extend ShopifyAPI::Webhooks::WebhookHandler

  def self.handle(topic:, shop:, body:, webhook_id:, api_version:)
    perform_later(topic: topic, shop_domain: shop, webhook: body)
  end

  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")
      
      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    shop.with_shopify_session do
    end
  end
end
```
