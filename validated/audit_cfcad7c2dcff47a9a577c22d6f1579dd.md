### Title
Webhook HMAC Verification Does Not Bind the Signature to `X-Shopify-Shop-Domain`, Enabling Cross-Shop Webhook Spoofing - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification` only verifies that the HMAC of the raw request body matches a signature computed with the app's shared secret; it never binds that signature to the `X-Shopify-Shop-Domain` header that downstream code uses to attribute the webhook to a specific merchant. Any merchant who has installed the app (and therefore legitimately receives real, correctly-signed webhooks for their own shop) can capture a valid `(body, HMAC)` pair and replay it while substituting an arbitrary shop domain in the header, causing the app to process attacker-supplied webhook data as if it originated from a different, victim shop. This is the same root-cause pattern as the external report: a value that the system implicitly trusts as “fresh/authentic” (`shop_domain`) is never actually checked/bound against the piece of data that establishes trust (the signature), so stale/forged context is accepted at face value.

### Finding Description
`hmac_valid?` computes and compares the HMAC over `data` (the raw POST body) only: [1](#0-0) 

`verify_request` calls `hmac_valid?(request.raw_post)` and, separately, `shop_domain` simply reads an unauthenticated header with no relationship to the signed payload: [2](#0-1) 

The documented pattern for custom webhook controllers explicitly trusts this unauthenticated `shop_domain` value to route processing to a specific shop: [3](#0-2) 

The same pattern appears in the generator templates that scaffold real app code, again keying job dispatch off the unauthenticated header value: [4](#0-3) [5](#0-4) 

Because `ShopifyApp.configuration.secret` (the app's client secret) is the same for every installed shop, any merchant who installs the app receives genuinely-signed webhooks for their own store. Since the signature covers only the body and never the domain header, that merchant can take a legitimately-signed body/HMAC pair and resend it to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header set to a different (victim) shop's domain. `verify_request` will pass because the HMAC still matches the (unchanged) body, and any handler using `shop_domain` (as shown in the docs/generator patterns) will process/attribute that payload to the victim shop.

### Impact Explanation
This allows cross-shop data injection/confusion: an attacker-controlled merchant can cause the app to execute webhook-triggered logic (e.g., data resync jobs, `shop_redact`/`customers_data_request` privacy jobs, order/product update jobs, or any custom shop-scoped job) against a victim shop record that the attacker does not own or have authorization to affect, using data of the attacker's choosing (the body content they replay). Depending on what the app's webhook jobs do with `shop_domain` + `webhook` payload, this can range from data corruption to unauthorized state changes attributed to the wrong tenant — a cross-shop access/integrity violation, matching the "cross-shop access" acceptance criterion.

### Likelihood Explanation
Any user who can install the app on a real or trial Shopify store gains an unprivileged foothold sufficient to obtain genuinely-signed webhook bodies for arbitrary topics. No cryptographic secret needs to be broken; the attacker only needs to swap an unauthenticated header on replay. This is a low-effort, realistically reachable path for any unrelated/anonymous merchant relative to the victim shop.

### Recommendation
Bind the shop identity into the verified payload rather than trusting the raw header: e.g., include the shop domain in the HMAC-covered data (or otherwise cross-validate the header against a value verified via signed JWT/session context), reject requests where the header-derived shop cannot be independently corroborated, and/or add replay protection (webhook ID/nonce + timestamp freshness window) so a captured signed payload cannot be resent for a different shop or at an arbitrary later time.

### Proof of Concept
1. Attacker installs the app on `attacker-shop.myshopify.com` and lets Shopify send a legitimate webhook, e.g. `orders/create`, capturing the raw body `B` and header `X-Shopify-Hmac-Sha256: H` (valid because `H = HMAC-SHA256(secret, B)`).
2. Attacker POSTs to the app's webhook endpoint (e.g. `/webhooks/orders_create`) with the same body `B` and header `X-Shopify-Hmac-Sha256: H`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
3. `verify_request` in [6](#0-5)  recomputes the HMAC over the same body `B` and it matches `H`, so the request is accepted (`head(:unauthorized)` is never called).
4. The controller/job reads `shop_domain` from the header (per the documented/generated pattern) and processes the attacker's order payload as if it belongs to `victim-shop.myshopify.com`.

### Citations

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L15-25)
```ruby
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
```

**File:** docs/shopify_app/webhooks.md (L86-104)
```markdown
If you'd rather implement your own controller then you'll want to use the [`ShopifyApp::WebhookVerification`](/lib/shopify_app/controller_concerns/webhook_verification.rb) module to verify your webhooks, example:

```ruby
class CustomWebhooksController < ApplicationController
  include ShopifyApp::WebhookVerification

  def carts_update
    params.permit!
    SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
    head :no_content
  end

  private

  def webhook_params
    params.except(:controller, :action, :type)
  end
end
```
```

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L1-13)
```text
# frozen_string_literal: true

module Webhooks
  class <%= @controller_class_name %> < ApplicationController
    include ShopifyApp::WebhookVerification

    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
    end
  end
end
```

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L1-21)
```text
class <%= @job_class_name %> < ActiveJob::Base
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

    shop.with_shopify_session do |session|
    ## webhook processing logic
    end
  end
end
```
