## Title
Webhook shop-domain header not covered by HMAC signature enables cross-shop webhook forgery - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
The `WebhookVerification` concern authenticates incoming webhooks by validating an HMAC over `request.raw_post` only, while the shop identity used later to select which merchant's record gets mutated (`X-Shopify-Shop-Domain`) is read from an unsigned HTTP header. Because the app's webhook signing secret is a single, app-wide value (not per-shop), any shop that installs the app can capture one of its own genuinely-signed webhook requests and replay the identical body/HMAC pair while swapping the shop-domain header to point at a different, victim shop.

### Finding Description
`ShopifyApp::PayloadVerification#hmac_valid?` computes the digest strictly over `data` (the raw POST body) and compares it to the `X-Shopify-Hmac-Sha256` header: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, and separately exposes `shop_domain` read directly from the `X-Shopify-Shop-Domain` header — a value that is never included in the signed payload: [2](#0-1) 

`WebhooksController#receive` forwards both the raw body and the full (unverified beyond HMAC-of-body) headers hash to `ShopifyAPI::Webhooks::Registry.process`, which dispatches to job handlers with `shop:` (derived from the header) as a distinct argument from the signed body: [3](#0-2) 

Every generated privacy/uninstall job template trusts this `shop_domain` value to look up and destructively act on a local `Shop` record, e.g.: [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

This mirrors LID-5 exactly: the value that drives downstream security-relevant behavior (`depositCalldata` in Lido, the shop-identifying domain here) is excluded from the cryptographic signature, so it can be substituted after a legitimately signed payload is obtained.

### Impact Explanation
Because the HMAC secret (`ShopifyApp.configuration.secret`) is a single app-level secret shared across all shops that install the app, an attacker who installs the app on their own store (an unprivileged, unrelated-merchant action) can obtain a validly-signed webhook body/HMAC pair from their own shop's traffic. They can then send the identical body and HMAC to the app's webhook endpoint while forging the `X-Shopify-Shop-Domain` header to name a victim shop. `hmac_valid?` still passes (it only checks the body), and the job is queued attributing the (attacker-controlled) payload to the victim's `shop_domain`. Depending on which webhook topic is abused, this yields cross-shop actions such as: deleting a victim's local `Shop` record via the uninstall job (`shop.destroy`), triggering shop/customer redaction workflows for a victim shop, or corrupting any app-specific webhook-driven data tied to `shop_domain` for a shop the attacker does not own.

### Likelihood Explanation
Likelihood is high for apps that use the shop-specific webhook manager (rather than exclusively app-specific declarative webhooks scoped server-side by Shopify) because: (1) the attacker needs no secrets beyond installing the app themselves, (2) capturing a valid (body, HMAC) pair from their own shop's traffic is trivial, and (3) none of the shipped controller/concern code cross-checks the header-derived shop against the signed body or against any authenticated session.

### Recommendation
Include the shop-identifying value in the signed material that is checked, or otherwise bind it cryptographically to the verified payload — e.g., verify that the webhook's declared shop domain is checked against the topic/payload contents when available. In `PayloadVerification`/`WebhookVerification`, compute/verify the HMAC over a canonical string that also incorporates the `X-Shopify-Shop-Domain` header (or equivalently, only trust `shop_domain` if it can be independently corroborated, such as by requiring the shop to already exist and be currently installed with matching webhook subscription state). At minimum, document and encourage per-shop-scoped verification, and add an explicit check that the shop implied by any embedded resource IDs in the payload matches the header-derived `shop_domain` before performing destructive actions.

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com`.
2. Attacker triggers/observes a webhook Shopify sends to the app (e.g., `app/uninstalled`), capturing the raw body `B` and the valid `X-Shopify-Hmac-Sha256` header `H` (computed by Shopify using the app's shared secret over `B`).
3. Attacker sends `POST /webhooks` (or the app's configured webhook endpoint) with body `B`, header `X-Shopify-Hmac-Sha256: H`, but header `X-Shopify-Shop-Domain: victim-shop.myshopify.com` and matching `x-shopify-topic`.
4. `WebhookVerification#verify_request` → `hmac_valid?(request.raw_post)` succeeds because it only checks `B` against `H`, per [9](#0-8) .
5. The dispatched job (e.g., `AppUninstalledJob`) receives `shop_domain: "victim-shop.myshopify.com"` and executes `Shop.find_by(shopify_domain: shop_domain).destroy`, per [10](#0-9) , deleting the victim shop's local record despite the attacker never installing on nor authenticating as that shop.

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L15-26)
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

**File:** lib/generators/shopify_app/add_privacy_jobs/templates/customers_data_request_job.rb.tt (L1-19)
```text
class CustomersDataRequestJob < ActiveJob::Base
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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_job.rb.tt (L1-14)
```text
class <%= @job_class_name %> < ActiveJob::Base

  def perform(shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")

      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    shop.with_shopify_session do |session|
    end
  end
```
