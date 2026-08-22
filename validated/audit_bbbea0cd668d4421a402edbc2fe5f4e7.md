Based on my investigation, I've confirmed the analog vulnerability.

### Title
Webhook shop identity (`X-Shopify-Shop-Domain`) is trusted unverified while only the raw body is HMAC-signed, allowing cross-shop webhook confusion - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` validates only that the raw POST body was HMAC-signed with the app's shared client secret [1](#0-0) . It never verifies that the `X-Shopify-Shop-Domain` header — the value used everywhere downstream to decide *which shop* the webhook belongs to — is bound to that signed body. This mirrors the reported bug class: the signed artifact (body+HMAC) is validated, but the "recipient"/target identity (`shop_domain`) supplied alongside it is not checked against the signer's intent, so a caller can supply their own valid signed body while pointing the identity field at a different shop.

### Finding Description
`ShopifyApp::WebhookVerification` computes HMAC validity purely from `request.raw_post`: [2](#0-1)  and separately exposes `shop_domain` by reading directly from the `X-Shopify-Shop-Domain` request header with no cryptographic binding to the verified body: [3](#0-2) .

Because the HMAC digest (`hmac_valid?`) is computed only over `data` (the raw body) using the app's client secret — which is shared across *every* shop that has installed the app, not scoped per shop — any merchant who has installed the app can capture a legitimately-signed webhook body that Shopify sent for their own store, then POST that exact body/HMAC pair to the app's webhook endpoint while forging the `X-Shopify-Shop-Domain` header (and `X-Shopify-Topic` header, also unverified) to reference an unrelated victim shop. The HMAC check still passes because it never inspects the header values.

The gem's own documented and generated integration pattern feeds this unverified `shop_domain` straight into privileged, shop-scoped background jobs:
- Documented custom-controller pattern: `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)` [4](#0-3) 
- Declarative webhook controller generator: `webhook_request.shop` is passed to the job unchanged [5](#0-4) 
- Privacy/GDPR job templates that look up and mutate a `Shop` record purely by the forged `shop_domain` value: `Shop.find_by(shopify_domain: shop_domain)` followed by `shop.destroy` (uninstall job) or a redaction session (`shop.with_shopify_session`) [6](#0-5) [7](#0-6) 

None of these consumers cross-check the `shop_domain` against the signed payload content itself; they trust the header exactly like `VVVVCTokenDistributor::claim` trusted `msg.sender` instead of the signer-attested `kycAddress`.

### Impact Explanation
An unrelated merchant (an app installer with no privileged relationship to the victim shop) can trigger shop-scoped webhook handlers — including the mandatory `shop/redact` and `app/uninstalled` jobs shipped by the generators — against any other shop that has the app installed, by replaying their own validly-HMAC'd webhook body with a forged `X-Shopify-Shop-Domain` header. Depending on the app's job implementation this can cause cross-shop data deletion (`shop.destroy`), spurious GDPR redaction/data-request processing for a shop the attacker doesn't own, or any other action keyed off the trusted `shop_domain`/`Shop.find_by` lookup — a confused-deputy cross-shop state change triggered by an accepted forged signed request.

### Likelihood Explanation
Exploitability only requires being an ordinary merchant who has installed the vulnerable app (to legitimately receive at least one HMAC-valid webhook body) plus the ability to send an arbitrary HTTP POST with custom headers to the app's public webhook endpoint — no secret knowledge, no privilege escalation, and no interaction with Shopify's servers is required, since the client secret is shared across all installs and the header fields are never part of the signed material.

### Recommendation
Do not trust `request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]` (or the topic header) as authoritative shop context. Either (a) include the shop domain/topic in the HMAC-covered material Shopify signs and re-derive/verify it server-side against the parsed body, or (b) cross-validate the header-derived `shop_domain` against an independently-verified source (e.g., the corresponding registered webhook subscription record for that topic/shop, or Shopify's webhook `X-Shopify-Webhook-Id` looked up via the Admin API) before using it for any authorization or data-scoping decision in generated jobs (`app_uninstalled_job`, `shop_redact_job`, `customers_redact_job`, `customers_data_request_job`, and the declarative webhook controller template).

### Proof of Concept
1. Install the vulnerable app on Shop A (attacker-controlled) and capture any legitimate webhook Shopify sends to the app's endpoint, noting the raw body and the `X-Shopify-Hmac-Sha256` header value.
2. Craft a new POST request to the same webhook endpoint using the identical raw body and `X-Shopify-Hmac-Sha256` header (still valid, since HMAC only covers the body and uses the shared app secret), but set `X-Shopify-Shop-Domain: shop-b.myshopify.com` (victim shop that also installed the app) and `X-Shopify-Topic: shop/redact` (or `app/uninstalled`).
3. `ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` [2](#0-1) , which passes because the body/HMAC pair is genuinely valid for the shared secret.
4. The controller/job enqueues `ShopRedactJob`/`AppUninstalledJob` with `shop_domain: "shop-b.myshopify.com"`; the job performs `Shop.find_by(shopify_domain: shop_domain)` and executes the privileged action (`shop.destroy`, redaction session) against Shop B, even though the request was never sent by Shopify for Shop B [8](#0-7) .

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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L7-10)
```text
    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
```

**File:** lib/generators/shopify_app/add_app_uninstalled_job/templates/app_uninstalled_job.rb.tt (L1-19)
```text
class AppUninstalledJob < ActiveJob::Base
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

    logger.info("#{self.class} started for shop '#{shop_domain}'")
    shop.destroy
  end
```

**File:** lib/generators/shopify_app/add_privacy_jobs/templates/shop_redact_job.rb.tt (L1-19)
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
```
