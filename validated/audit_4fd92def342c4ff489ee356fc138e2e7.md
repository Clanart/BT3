### Title
Webhook shop-identity spoofing via unauthenticated `X-Shopify-Shop-Domain` header bypasses HMAC scope, enabling cross-shop destructive actions - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification#verify_request` only HMACs the raw request body, never the shop-identifying header. The concern separately exposes a `shop_domain` helper that simply reads the unauthenticated `X-Shopify-Shop-Domain` header, and this exact pattern is documented and baked into ShopifyApp's own generated job templates as the tenant key for destructive/privileged actions (deleting a shop record, triggering GDPR redaction). This mirrors the PhiFactory bug class: the "signature" (HMAC) only covers a subset of the data actually trusted downstream (the body), while another attacker-controlled field (the shop-domain header) is accepted and used for authorization/tenant selection without being part of the signed material.

### Finding Description
`hmac_valid?` computes the HMAC over `request.raw_post` only: [1](#0-0) 

`WebhookVerification#verify_request` calls this with `data = request.raw_post`, and `shop_domain` reads directly from the `X-Shopify-Shop-Domain` header — a value that is never included in the HMAC computation: [2](#0-1) 

The official docs explicitly instruct developers to dispatch background jobs keyed on this unverified `shop_domain`: [3](#0-2) 

The gem's own generated job templates follow this exact pattern for destructive, tenant-scoped operations — using the `shop` (from header) to look up and then destroy or redact a shop: [4](#0-3) [5](#0-4) 

Because the app's webhook secret (`ShopifyApp.configuration.secret`) is a single shared client secret used to sign webhooks for *every* shop that installs the app, any attacker who legitimately receives one valid `(body, HMAC)` pair for their own shop (e.g., by installing/uninstalling the app on a shop they control, or intercepting any webhook delivery) can replay that exact body and HMAC to the public webhook endpoint while forging the `X-Shopify-Shop-Domain` header to name a victim shop. `hmac_valid?` passes because the body/secret pair is genuinely valid — it never checked which shop the header claims. The downstream job then acts on the attacker-chosen `shop_domain`, not the shop whose webhook was actually signed.

### Impact Explanation
This allows an unprivileged party (any merchant who has installed the target app on their own store, i.e. an "unrelated-merchant" from the victim's perspective) to forge a request that the app's own verification path accepts as authentic, and to redirect its tenant-scoped destructive effect at an arbitrary other shop. Using the gem's own generated templates as the concrete instance: replaying a captured `app/uninstalled` webhook body+HMAC with a forged shop header causes `Shop.find_by(shopify_domain: shop_domain); shop.destroy` to delete a different merchant's shop record; replaying a `shop/redact` webhook similarly triggers `shop.with_shopify_session` privacy-redaction flow against an arbitrary victim shop. This is a cross-shop authorization bypass with data-destruction/redaction impact caused by accepting a forged/spoofable identity field alongside a signature that doesn't cover it.

### Likelihood Explanation
Likelihood is moderate-to-high in apps that follow ShopifyApp's own documented and generated pattern (which is the common case, since it ships in the generators). The attacker doesn't need the app secret — only a single genuine webhook delivery for a shop they control, which is trivially obtainable (install/uninstall the app, or any webhook topic). No additional bypass of TLS or shopify_api internals is needed; the vulnerable surface is entirely in shopify_app's own `PayloadVerification`/`WebhookVerification` concerns and generated templates.

### Recommendation
Bind the shop identity into the authenticated material, or otherwise cryptographically tie the header to the verified request, e.g.:
- Require/verify that any shop-domain-bearing header used for job dispatch matches a value embedded in the HMAC-signed body (where the webhook topic includes shop info), or
- Look up shop context strictly from data returned by `ShopifyAPI::Webhooks::Request`/`Registry.process` after using the built-in, more robust request parser rather than trusting a raw header via a custom `shop_domain` helper, and
- Document explicitly that `shop_domain` from `WebhookVerification` must not be used as the sole tenant/authorization key for privileged actions without additional cross-checks (e.g., correlating against an active session or shop record keyed by an id present in the signed body).

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com`, then uninstalls it, causing Shopify to deliver a legitimate `app/uninstalled` webhook: body `B`, header `X-Shopify-Hmac-Sha256: H` (valid HMAC of `B` under the shared app secret), header `X-Shopify-Shop-Domain: attacker.myshopify.com`.
2. Attacker captures `(B, H)` (e.g., from their own webhook receiver logs).
3. Attacker POSTs directly to the app's webhook endpoint with the exact body `B`, header `X-Shopify-Hmac-Sha256: H` (unchanged, still valid since `hmac_valid?` only checks `B` against the shared secret), and forged header `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
4. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` → passes because `B`/`H` are genuinely valid for the shared secret.
5. The controller queues `AppUninstalledJob.perform_later(shop_domain: "victim-shop.myshopify.com", webhook: B)` per the documented/generated pattern.
6. `AppUninstalledJob#perform` executes `Shop.find_by(shopify_domain: "victim-shop.myshopify.com").destroy` — the victim's shop record is deleted despite them never having uninstalled anything.

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
