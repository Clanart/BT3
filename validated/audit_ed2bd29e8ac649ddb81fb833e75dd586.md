I have enough information to finalize the analog finding.

### Title
Webhook shop-domain header is not covered by HMAC verification, allowing cross-shop webhook forgery - (File: lib/shopify_app/controller_concerns/webhook_verification.rb, lib/shopify_app/controller_concerns/payload_verification.rb)

### Summary
The reported bug class is: an identity/tenant value (`sender`) is read from unauthenticated request data and used as if it were verified, instead of being checked against a securely authenticated identity. In `shopify_app`, the webhook pipeline has the same structural flaw: the HMAC signature only covers the raw request body, while the shop identity (`X-Shopify-Shop-Domain` header, and the equivalent `shop` field the `ShopifyAPI::Webhooks::Request` derives from headers) is never included in, or bound to, that signature.

### Finding Description
`ShopifyApp::PayloadVerification#hmac_valid?` computes the HMAC exclusively over `data` (i.e. `request.raw_post`) using the app's shared secret: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` and, separately, exposes `shop_domain` by reading it straight from the `X-Shopify-Shop-Domain` header with no cryptographic tie to the verified body: [2](#0-1) 

The default `WebhooksController` builds a `ShopifyAPI::Webhooks::Request` from `request.raw_post` and `request.headers.to_h` and hands it to `ShopifyAPI::Webhooks::Registry.process`, which likewise derives the shop from headers, not from the signed payload: [3](#0-2) 

The generated declarative-webhook controller template and the recommended custom-controller pattern both propagate this same unauthenticated `shop`/`shop_domain` value directly into background job parameters used for tenant-scoped processing: [4](#0-3) [5](#0-4) 

The generated job template then uses this attacker-influenceable `shop_domain` to look up the `Shop` record and run app logic in its authenticated Shopify session context: [6](#0-5) 

Because the webhook secret (`ShopifyApp.configuration.secret`) is a single app-level client secret shared by every shop that installs the app — not a per-shop secret — any merchant who installs the app can trigger a legitimate webhook for their own shop, capture the resulting valid `raw_post` + `X-Shopify-Hmac-Sha256` pair, and replay it to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header with a victim shop's domain. `hmac_valid?` still returns true (the body and secret are unchanged), so `verify_request` passes, and the forged shop identity flows unauthenticated into `shop_domain`/`webhook_request.shop` and ultimately into tenant-scoped job processing — the same class of bug as the reported issue: an unauthenticated "sender"/tenant identifier taken from data and trusted as if verified.

### Impact Explanation
An attacker who is a merchant of the target app (i.e., "unrelated-merchant" with respect to the victim shop, requiring no special privilege on the victim) can forge webhook events attributed to any other shop that has installed the app. Depending on the app's webhook handlers (e.g., `orders/create`, `app/uninstalled`, `customers/data_request`, `shop/redact`), this can trigger unauthorized state changes, data processing, or privacy actions scoped to a victim shop, i.e., a cross-shop/cross-tenant forged-request acceptance.

### Likelihood Explanation
Likelihood is moderate-to-high in any app relying on the shipped `WebhooksController`/`WebhookVerification`/`PayloadVerification` concerns (the gem's documented, recommended pattern) without adding their own binding between the verified body and the shop header. No secret leak or insider access is required — only the ability to install the app on an attacker-controlled shop and observe one legitimate webhook delivery, which is standard app usage.

### Recommendation
Bind the verified identity to the signed payload instead of trusting the header/derived `shop` value independently. Concretely: verify that the `shop`/`shop_domain` extracted from the webhook is consistent with data the app can independently authenticate (e.g., cross-check against `X-Shopify-Webhook-Id` uniqueness plus a shop that is actually installed, or better, ensure the HMAC verification is shop-scoped where per-shop secrets are available, and always validate that the shop present in the header corresponds to a shop record already known to legitimately receive that specific `webhook_id`/topic combination) rather than blindly forwarding `shop_domain`/`request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]` into job parameters used for tenant lookup and session activation.

### Proof of Concept
1. Attacker installs the app on their own shop `attacker.myshopify.com` and triggers a webhook event (e.g., updates an order), causing Shopify to POST a validly HMAC-signed webhook to the app's endpoint.
2. Attacker captures the raw POST body and the `X-Shopify-Hmac-Sha256` header value from this legitimate delivery (e.g., via a local proxy/tunnel they control).
3. Attacker replays the exact same body + HMAC header to the app's `/webhooks/:type` endpoint, but changes the `X-Shopify-Shop-Domain` header to `victim.myshopify.com`.
4. `WebhookVerification#verify_request` → `PayloadVerification#hmac_valid?` validates successfully because it only checks the raw body against the shared app secret [1](#0-0) .
5. The request is processed and `shop_domain`/`webhook_request.shop` returns `victim.myshopify.com`, which is passed into `SomeJob.perform_later(shop_domain: shop_domain, webhook: ...)` per the gem's own documented pattern [7](#0-6) , causing the job to run against `victim`'s shop record and session.

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

**File:** docs/shopify_app/webhooks.md (L88-104)
```markdown
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

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L1-20)
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
```
