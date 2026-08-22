## Finding: Webhook shop attribution is not covered by HMAC verification, allowing cross-shop webhook spoofing

### Title
Webhook `shop_domain` is taken from an unauthenticated header not covered by the HMAC signature, enabling cross-shop webhook forgery - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` validates the authenticity of a webhook request solely by checking the HMAC of the raw request body against the app's shared secret. The shop attribution (`X-Shopify-Shop-Domain` header), which is what downstream job code uses to decide *which shop's data* the webhook applies to, is never included in that HMAC computation. This is the same class of bug as the reported LayerZero issue: an identity/attribution value (there, `ownerAddress`; here, `shop_domain`) is derived from a channel that isn't cryptographically bound to the "signed" operation, so it can be substituted by an attacker while the signature still validates.

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` computes/compares the HMAC exclusively over `request.raw_post`: [1](#0-0) 

`WebhookVerification#verify_request` gates the request purely on this body HMAC: [2](#0-1) 

The shop identity used by application code to route/process the webhook (`shop_domain`, or `webhook_request.shop` in the generator template) comes straight from an HTTP header that is not part of the signed payload: [3](#0-2) 

The generated webhook controller and job templates trust this header-derived shop value directly to select which local `Shop` record's data gets mutated: [4](#0-3) [5](#0-4) 

Because the app's webhook secret is shared across every shop that installs the app (it is the app's client secret, not a per-shop secret), any merchant who installs the app receives genuinely-signed webhooks for their own shop. That merchant can capture a `(raw_body, X-Shopify-Hmac-Sha256)` pair from a legitimate webhook Shopify sent them, then replay the identical body/HMAC to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header (and/or `shop`/`webhook_request.shop`) to name a different, victim shop that also has the app installed. `hmac_valid?` will still succeed because it only checks the untouched body, and the app will process/attribute the payload as belonging to the victim shop.

### Impact Explanation
This lets any shop that has installed the app forge webhook events attributed to another shop using the same app instance. Depending on what the app's webhook job does (e.g., updating local records keyed by `shop_domain`, triggering `shop.with_shopify_session` API calls, or handling mandatory privacy webhooks like `customers/redact`, `shop/redact`), this can cause cross-shop data corruption, spurious actions being performed against a victim shop's Shopify session, or privacy-webhook logic being triggered for the wrong shop.

### Likelihood Explanation
Any merchant who installs the app (a low bar — no special privilege, no existing relationship with the victim) can trigger this once they've captured one legitimate webhook of their own. No secrets need to be leaked; the flaw is structural, since the shop attribution header is simply outside the scope of what `hmac_valid?` checks.

### Recommendation
Do not trust the `X-Shopify-Shop-Domain` header (or `webhook_request.shop`) as an authenticated value on its own. Either:
- Bind shop identity into the verification step by cross-checking the header/derived shop against a shop identifier embedded in the verified webhook body (when Shopify includes one), or
- At minimum, verify that the resolved shop actually corresponds to a shop with an active, matching webhook registration/session before acting on the payload, rather than trusting the header outright for record selection.

### Proof of Concept
1. Attacker installs the app on `attacker-shop.myshopify.com`, causing Shopify to send a legitimately signed webhook (e.g. `orders/create`) to the app's webhook endpoint, with headers `X-Shopify-Hmac-Sha256: <valid-hmac>` and `X-Shopify-Shop-Domain: attacker-shop.myshopify.com`, and some raw JSON body `B`.
2. Attacker replays a POST to the same webhook endpoint with the identical body `B` and identical `X-Shopify-Hmac-Sha256`, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (a shop also using the app).
3. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which only checks body `B` against the shared secret — it passes.
4. The controller/job (per `lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt` and `add_webhook/templates/webhook_job.rb.tt`) looks up `Shop.find_by(shopify_domain: "victim-shop.myshopify.com")` and processes attacker-controlled data `B` as if it originated from the victim shop.

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L23-25)
```ruby
    def shop_domain
      request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]
    end
```

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L1-12)
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
