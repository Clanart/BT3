### Title
Webhook HMAC verification does not bind the signature to the `X-Shopify-Shop-Domain` header, enabling cross-shop webhook replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
The externally reported bug is that `ERC20Permit`'s signed payload omits `chainID`, so a signature valid on one chain remains valid after a fork and can be replayed on the other chain because the signature doesn't bind to the execution context. The analogous defect in `shopify_app` is in `ShopifyApp::WebhookVerification`/`ShopifyApp::PayloadVerification`: the webhook signature (`X-Shopify-Hmac-Sha256`) is computed only over the raw POST body, and the shop-identifying context (`X-Shopify-Shop-Domain` header) is read separately and is not part of the signed data. This missing "domain separator" allows a request with a genuinely valid HMAC (for one shop's payload) to be replayed with a different `shop_domain` value, letting the app attribute the payload to a different, unrelated shop.

### Finding Description
`ShopifyApp::PayloadVerification#hmac_valid?` computes/validates the HMAC strictly over `request.raw_post`: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` and, separately, exposes `shop_domain` from a header that is never included in the HMAC input: [2](#0-1) 

The official documented custom-controller pattern for handling webhooks explicitly trusts this unverified `shop_domain` helper to route processing to a specific shop: [3](#0-2) 

Because the app's webhook secret (`ShopifyApp.configuration.secret`/`old_secret`) is a single shared secret for the whole app (not per-shop), any merchant who has installed the app can trigger a real Shopify webhook to their own store (e.g. `products/update`, `app/uninstalled`, `shop/redact`) and legitimately receive a `(body, valid HMAC)` pair from Shopify. Because `shop_domain` is not covered by the signature, that same `(body, HMAC)` pair can be replayed with the `X-Shopify-Shop-Domain` header changed to an arbitrary victim shop domain: `verify_request` will still pass, since it only re-derives the HMAC over the (unchanged) body.

Generated job handlers that trust this header to select the target tenant record illustrate the downstream impact, e.g. `AppUninstalledJob`/`ShopRedactJob` look up and destroy/redact a `Shop` purely by the (attacker-controlled) `shop_domain` value passed from the controller: [4](#0-3) [5](#0-4) 

The same signed-data/header split exists in `ShopifyApp::ExtensionVerificationController`, which relies on the identical `hmac_valid?` helper: [6](#0-5) 

### Impact Explanation
An attacker who controls one shop (an "unrelated merchant" relative to the victim) can forge a webhook request that the app will process as if it originated from an arbitrary victim shop domain, because the header carrying tenant context is outside the cryptographically verified scope — directly analogous to a chain-fork replay where the signed payload is valid outside its intended context. Depending on the app's own webhook job implementation (following the documented pattern), this can lead to cross-shop state corruption, spoofed uninstall/redact actions against a shop the attacker does not own, or misattributed data processing.

### Likelihood Explanation
Exploitation only requires the attacker to install the app on a shop they control (a normal, unprivileged merchant action) and to know/guess a target shop's `myshopify.com` domain, which is often discoverable or guessable. No secret material needs to be extracted; the attacker leverages a legitimately issued HMAC for content they control and simply swaps an unauthenticated header value.

### Recommendation
Bind the webhook shop context into the verified signature computation instead of trusting an unauthenticated header: incorporate the `X-Shopify-Shop-Domain` (and/or `X-Shopify-Webhook-Id`) value into the data passed to `hmac_valid?`, or explicitly verify that the shop asserted by the header matches the shop identifiable from already-authenticated data (e.g., a session/installation record) before dispatching to shop-scoped job logic. More generally, treat any header/param that determines tenant routing as part of the trust boundary that must be covered by the signature, mirroring the "include chainID in the signed payload" remediation from the original report.

### Proof of Concept
1. Install the target app on an attacker-owned shop `attacker.myshopify.com` and trigger any registered webhook topic (e.g., update a product) so Shopify sends a real webhook: body `B`, header `X-Shopify-Hmac-Sha256: H` (valid, computed with the shared app secret over `B`), header `X-Shopify-Shop-Domain: attacker.myshopify.com`.
2. Capture this request (`B`, `H`).
3. Resend the identical body `B` and HMAC header `H` to the app's webhook endpoint, but replace `X-Shopify-Shop-Domain` with `victim.myshopify.com`.
4. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which only checks `B` against `H` — this still succeeds because `B` and `H` are unchanged, per: [1](#0-0) 
5. The app's job handler (built per the documented pattern) receives `shop_domain: "victim.myshopify.com"` and processes/destroys/redacts data for the victim shop despite the payload never having been signed for that shop.

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

**File:** docs/shopify_app/webhooks.md (L88-103)
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

**File:** app/controllers/shopify_app/extension_verification_controller.rb (L1-18)
```ruby
# frozen_string_literal: true

module ShopifyApp
  class ExtensionVerificationController < ActionController::Base
    include ShopifyApp::PayloadVerification
    protect_from_forgery with: :null_session
    before_action :verify_request

    private

    def verify_request
      unless hmac_valid?(request.raw_post)
        head(:unauthorized)
        ShopifyApp::Logger.debug("Extension verification failed due to invalid HMAC")
      end
    end
  end
end
```
