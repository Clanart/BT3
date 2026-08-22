### Title
Webhook HMAC verification does not bind the shop-domain header, allowing forged/cross-shop webhook replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`UniERC20.uniTransferFrom` was flagged because for ETH transfers the function silently ignores the caller-supplied `from`/`to` parameters and instead trusts an implicit value (`msg.sender`/`this`), so if it is ever invoked with unexpected parameters the actual transfer diverges from what the parameters imply. The same class of bug — a security-relevant identity value that looks validated but is not actually bound to the cryptographic check — exists in shopify_app's webhook verification path: the HMAC signature only covers the raw request body, while the shop identity used by generated app code (`shop_domain`) comes from an unsigned HTTP header.

### Finding Description
`ShopifyApp::PayloadVerification#hmac_valid?` computes the HMAC only over `request.raw_post`: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` uses exactly this check, and the module separately exposes `shop_domain`, read straight from the `X-Shopify-Shop-Domain` header, with no cryptographic link to the HMAC: [2](#0-1) 

Because the HMAC digest is computed over the body only, the `X-Shopify-Shop-Domain` header (and other headers such as topic) can be modified freely without invalidating the signature. A party that has received one genuinely Shopify-signed webhook body for their own shop (e.g., after installing the app on their own store) can resend that exact body to the app's webhook endpoint while swapping the shop-domain header to any other shop's `myshopify.com` domain. `hmac_valid?` will still return true because it never inspects the header.

Generated job templates then use this unauthenticated `shop_domain` value as the sole tenant identifier to look up and mutate another shop's record. The most severe instance is the uninstall job template, which destroys the `Shop` record matched purely on the forged `shop_domain`: [3](#0-2) 

The same unauthenticated-domain pattern (`Shop.find_by(shopify_domain: shop_domain)` followed by acting `with_shopify_session`) is repeated in the general webhook job and declarative webhook job templates: [4](#0-3) [5](#0-4) 

This mirrors the reported bug class exactly: the function/flow *appears* to authenticate the full request (`verify_request`/HMAC check gives the impression of full request integrity), but a security-sensitive parameter (`shop_domain`) that later drives privileged, tenant-scoped actions is not actually covered by that verification.

### Impact Explanation
An attacker who is a legitimate but unrelated merchant (or anyone able to capture one valid Shopify-signed webhook delivered to the app, e.g., by installing/uninstalling the app on their own store) can replay that body with a forged `X-Shopify-Shop-Domain` header pointing at a victim shop. Because the generated uninstall handler trusts `shop_domain` to identify and destroy the corresponding `Shop` record, this allows an unrelated merchant to trigger deletion of another shop's app data/session (denial of service, forced re-auth, data loss) without ever installing the app on the victim's store. Other webhook jobs that key business logic off `shop_domain` inherit the same cross-shop trust problem.

### Likelihood Explanation
Exploitation requires only capturing/replaying one legitimately-signed webhook body (readily obtainable by installing the app on an attacker-controlled shop and triggering any subscribed webhook, or uninstalling it to get an `app/uninstalled` payload) and modifying an HTTP header before resending it to the app's public webhook endpoint. No secret material or privileged access is required beyond what any unrelated merchant already has, so likelihood is moderate-to-high for apps that rely on the generated job templates as-is.

### Recommendation
Do not derive the acting shop's identity from the `X-Shopify-Shop-Domain` header (or any other header) independently of the HMAC-covered payload. Either:
- Include the shop domain in the HMAC-signed computation/verification (e.g., verify it against the `shop` field embedded in the signed webhook body where available), or
- Cross-check the header-derived `shop_domain` against a shop that is actually known/installed with a currently valid session before performing any destructive or state-changing action, and reject webhooks whose header shop does not match verifiable session state.
At minimum, update the generator templates (`add_app_uninstalled_job`, `add_webhook`, `add_declarative_webhook`) so destructive operations are not solely gated on the unauthenticated `shop_domain` value.

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com` and later uninstalls it, causing Shopify to deliver a genuine, HMAC-signed `app/uninstalled` webhook: body `B`, header `X-Shopify-Hmac-Sha256: H(secret, B)`, header `X-Shopify-Shop-Domain: attacker.myshopify.com`.
2. Attacker captures this request and resends it to the same app webhook endpoint, keeping body `B` and header `X-Shopify-Hmac-Sha256: H(secret, B)` unchanged, but replacing `X-Shopify-Shop-Domain` with `victim.myshopify.com`.
3. `ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which recomputes `H(secret, B)` and matches the unchanged signature header — verification passes.
4. `ShopifyAPI::Webhooks::Registry.process` dispatches to `AppUninstalledJob.handle`, which forwards `shop: "victim.myshopify.com"` (from the forged header) to `perform_later`.
5. `AppUninstalledJob#perform` runs `Shop.find_by(shopify_domain: "victim.myshopify.com")` and calls `shop.destroy`, deleting the victim shop's record even though the app was never uninstalled (or even installed) there by the victim.

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
