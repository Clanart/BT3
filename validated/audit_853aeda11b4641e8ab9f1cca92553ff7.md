This confirms the analog: the `x-shopify-shop-domain` header used to route the webhook job to a specific shop is **not covered by the HMAC signature check** at all — `hmac_valid?` only validates `request.raw_post` (the body), while `shop_domain`/topic attribution comes from unauthenticated headers.## Finding

### Title
Webhook shop attribution is not covered by HMAC signature, enabling cross-shop signature replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` only HMAC-verifies `request.raw_post` (the webhook body). The `X-Shopify-Shop-Domain` header, which is used downstream to route the webhook to a specific shop's record, is never included in the signed data. This mirrors the reported `WaveContract` bug class: a signature valid for one context (one shop's webhook) can be replayed against a different context (a different shop) because the identifier that scopes the request is outside the signed payload.

### Finding Description
`ShopifyApp::PayloadVerification#hmac_valid?` computes/validates the HMAC strictly over `data` (the raw request body): [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` and separately exposes `shop_domain` from an unsigned header: [2](#0-1) 

`WebhooksController#receive` passes both the raw body and the full (unverified beyond HMAC) header set to `ShopifyAPI::Webhooks::Registry.process`, which dispatches to job classes with a `shop:` value derived from headers, not from anything covered by the HMAC: [3](#0-2) 

Generated job templates then trust this `shop_domain` value directly for destructive/privileged operations, e.g. deleting a shop's record on `shop/redact`: [4](#0-3) 

Because the HMAC only proves "this body was signed by Shopify with the app secret" and not "for this shop/topic/webhook_id", a body+HMAC pair captured from one legitimately-delivered webhook (e.g. from a shop the attacker owns/controls, or one they can observe) remains a **valid signature** even when replayed with a different `X-Shopify-Shop-Domain` header. This is the exact analog of the reported bug: signatures aren't bound to the "wave" (here: the specific shop/topic instance), so the same signature validates across different contexts.

### Impact Explanation
An attacker who controls or can observe any one legitimately-signed webhook delivery (trivially achievable by installing the app on their own store and capturing their own webhook traffic) can replay that exact body+HMAC pair while substituting the `X-Shopify-Shop-Domain` header for a victim shop. Depending on which webhook job is targeted, this can cause cross-shop state mutation attributed to a shop the attacker doesn't own — including privacy/redaction jobs that call `shop.destroy` or delete customer data, or app-specific jobs that write attacker-controlled body content into a victim shop's records. This is a cross-shop integrity/authorization violation reachable via an unauthenticated public webhook endpoint.

### Likelihood Explanation
Medium: it requires the attacker to already possess one validly-signed webhook payload (easy — install the app on any shop they control, which is normal, unprivileged usage) and to know/guess a valid target shop domain and matching job/topic semantics that don't otherwise validate the payload against the claimed shop. Exploitability depends on the specific `handle`/`perform` implementation trusting `shop_domain` without further business-logic checks, which several bundled generator templates do (e.g., `shop_redact_job.rb.tt`, `customers_redact_job.rb.tt`, `app_uninstalled_job.rb.tt`).

### Recommendation
Bind the shop context into the verified data, mirroring the `WaveContract` fix of signing `address(this)`. Concretely:
- Extend `PayloadVerification#hmac_valid?`/`WebhookVerification#verify_request` to also verify that the `X-Shopify-Shop-Domain` (and ideally `X-Shopify-Webhook-Id`/topic) values are consistent with data Shopify actually signed, or
- At minimum, document/enforce that webhook job handlers must independently authenticate the shop (e.g., confirm the shop has an active session/webhook registration and that the specific `webhook_id` hasn't been processed before) rather than trusting `shop_domain` from headers alone.

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com` and triggers a webhook (e.g. `shop/redact`), capturing the raw body `B` and its valid `X-Shopify-Hmac-Sha256` header `H` (both computed by Shopify using the app's shared secret — the same secret used for all shops).
2. Attacker crafts a new POST to the app's `/webhooks/shop_redact` (or equivalent) endpoint with the same body `B` and header `H`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
3. `WebhookVerification#verify_request` ( [5](#0-4) ) validates `hmac_valid?(B)` successfully since `B` and `H` are unchanged and the secret is shared, ignoring the forged shop header.
4. `Registry.process` dispatches to `ShopRedactJob.handle(shop: "victim-shop.myshopify.com", ...)`, and `ShopRedactJob#perform` looks up and destroys the victim shop's record ( [6](#0-5) ), even though the signed payload never authenticated that shop.

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

**File:** lib/generators/shopify_app/add_privacy_jobs/templates/shop_redact_job.rb.tt (L1-20)
```text
class ShopRedactJob < ActiveJob::Base
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
