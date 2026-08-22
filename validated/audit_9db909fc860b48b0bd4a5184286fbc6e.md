Confirmed: `AppUninstalledJob#perform` in `lib/generators/shopify_app/add_app_uninstalled_job/templates/app_uninstalled_job.rb.tt` calls `shop.destroy` keyed purely on the `shop_domain` value that traces back to the unsigned `X-Shopify-Shop-Domain` header, giving a concrete destructive action tied to unauthenticated attacker-controlled data.

### Title
Webhook Shop Attribution Not Covered by HMAC Allows Cross-Shop Webhook Forgery/Replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` computes the webhook HMAC only over `request.raw_post`, while the shop that the webhook is attributed to is read from the separate, unsigned `X-Shopify-Shop-Domain` header via the `shop_domain` helper [1](#0-0) . Because the shared app secret used for the HMAC is identical for every shop that installs the app (it is not per-shop), any merchant who legitimately receives a validly-signed webhook for their own shop can replay that exact body/HMAC pair to the app's public webhook endpoint while substituting a different value in the `X-Shopify-Shop-Domain` header. The signature will still validate, but the downstream job will process the data as if it belongs to the attacker-chosen shop.

### Finding Description
`hmac_valid?` in `PayloadVerification` verifies only the raw POST body against the shared app secret [2](#0-1) . `WebhookVerification#shop_domain` and the generated `WebhooksController`/declarative webhook controllers pass this header-derived value straight into background jobs as `shop_domain:` without any additional binding to the signed payload [3](#0-2) . The generated job templates then use that unauthenticated `shop_domain` to look up and act on a specific `Shop` record, e.g. `Shop.find_by(shopify_domain: shop_domain)` followed by `shop.destroy` in the app-uninstalled job template [4](#0-3) , and similarly for the privacy-redaction job templates [5](#0-4) . This mirrors the reported bug class exactly: the value that determines the ultimate effect (`shop_domain`, analogous to `final_amount`) is not part of the data covered by the signature check, so a party who can obtain one validly-signed request can substitute their preferred value for that field.

### Impact Explanation
A merchant who has installed the app receives real, validly-HMAC'd webhooks for their own shop (including `app/uninstalled` when they uninstall). By resending that captured request to the app's public webhook endpoint with the `X-Shopify-Shop-Domain` header changed to a victim shop's domain, the HMAC check still passes (it never inspected the header), and the job is queued attributing the payload to the victim shop. For the generated `AppUninstalledJob`, this results in `shop.destroy` being executed against the victim's stored `Shop` record — deleting their access token/session and forcing them into a broken/uninstalled state (denial of service and forced re-authorization) without the victim doing anything. The same primitive applies to any other webhook-driven job that trusts `shop_domain` (e.g., customer/shop redaction jobs), potentially triggering data deletion against an unrelated merchant's account.

### Likelihood Explanation
Exploitation only requires the attacker to be any merchant who has installed the app (an "unrelated merchant" from the victim's perspective) — no access to the app's secret, admin credentials, or the victim's data is required. They simply capture one legitimate webhook sent to their own endpoint and replay it with a modified header. The webhook endpoint is a public, unauthenticated HTTP endpoint by design, and topic/shop routing headers are trusted as-is by `ShopifyApp::WebhooksController` and every generator-produced controller/job pair.

### Recommendation
Do not rely solely on the `X-Shopify-Shop-Domain` (or `X-Shopify-Topic`) header to determine which shop record a job should act on. Cross-check the shop domain against data embedded in the verified body where possible, bind webhook processing to a `webhook_id` idempotency/replay check, and/or require that the resolved shop actually correspond to an install that is currently expected to receive that topic (e.g., verify the shop has an active session before performing destructive actions like `shop.destroy`).

### Proof of Concept
1. Install the target app on shop `attacker.myshopify.com` and capture a legitimate `app/uninstalled` webhook request Shopify sends, including its `X-Shopify-Hmac-Sha256` header (valid since HMAC is computed with the app's single shared secret over the body only).
2. Replay that exact HTTP request to the app's `/webhooks/app_uninstalled` endpoint, changing only the `X-Shopify-Shop-Domain` header to `victim.myshopify.com`.
3. `WebhookVerification#verify_request` validates the HMAC successfully (body unchanged) [6](#0-5) ; `AppUninstalledJob#perform` looks up `Shop.find_by(shopify_domain: "victim.myshopify.com")` and calls `shop.destroy`, deleting the victim's stored session [4](#0-3) .

### Citations

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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L7-10)
```text
    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
```

**File:** lib/generators/shopify_app/add_app_uninstalled_job/templates/app_uninstalled_job.rb.tt (L8-19)
```text
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

**File:** lib/generators/shopify_app/add_privacy_jobs/templates/customers_redact_job.rb.tt (L1-19)
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
```
