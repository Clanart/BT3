## Finding: Webhook tenant identity (`shop_domain`) is not covered by HMAC verification, enabling cross-shop webhook spoofing

### Title
Webhook `shop_domain` helper is unauthenticated, allowing an unrelated merchant to spoof another shop's tenant context in webhook-triggered jobs - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` authenticates that a webhook *body* was signed by Shopify using the shared secret, but the shop identity used to route/process that webhook (`shop_domain`) is read from an HTTP header that is **not** part of the signed payload. This mirrors the Magnetar bug class: the system correctly verifies that a request is "approved" (HMAC-valid), but fails to bind that approval to the specific tenant/user it is being applied to, letting an attacker apply another party's approval/authenticated context to their own attacker-chosen target.

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` computes the HMAC strictly over `request.raw_post` (the body): [1](#0-0) 

`WebhookVerification#verify_request` only checks this body HMAC before allowing the request through, and separately exposes a `shop_domain` helper that reads directly from the unsigned `X-Shopify-Shop-Domain` header: [2](#0-1) 

The gem's own documentation recommends exactly this pattern for custom webhook controllers - passing the header-derived, unauthenticated `shop_domain` straight into a background job: [3](#0-2) 

Every generated webhook job template then uses this `shop_domain` to look up the `Shop` record and open an authenticated session with **that shop's real stored access token**: [4](#0-3) [5](#0-4) 

Because the `X-Shopify-Shop-Domain` (and `X-Shopify-Topic`) headers are not covered by the body HMAC, any party who can obtain **one validly-signed webhook body** (e.g. by installing the app on their own store and triggering a webhook for a topic they control) can replay that request to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header value with a victim shop's domain. `hmac_valid?` still passes because it only checks the body against the shared secret, and the job is dispatched with an attacker-chosen `shop_domain` naming the victim.

### Impact Explanation
This lets an attacker who has legitimately installed the app on their own store (an "unrelated merchant") make the app execute webhook-processing logic in the context of a victim shop it did not receive that data from - i.e., forcing the app to `Shop.find_by(shopify_domain: victim_domain)` and run `shop.with_shopify_session` (using the victim's real stored offline access token) with attacker-supplied webhook body content. Depending on which job is targeted, this can range from data corruption (job logic mutating victim shop state based on attacker payload) up to shop deletion via the generated `AppUninstalledJob`, which calls `shop.destroy` unconditionally once it resolves the (spoofable) `shop_domain`: [6](#0-5) 

This is a cross-tenant authorization bypass: the HMAC only proves "this body came from Shopify for *some* topic," not "this body concerns shop X" - exactly the same root cause class as the Magnetar report (a valid cryptographic/approval check exists, but it is not bound to the specific principal the operation is performed against).

### Likelihood Explanation
Exploitation requires only that the attacker be able to install the app on a shop they control (any developer/partner account, or even a free development store) and capture one valid webhook delivery for any subscribed topic - both trivially achievable by an "unrelated merchant." No secret material needs to be forged; only headers, which are unsigned, need to be swapped in the replay. The vulnerable pattern (`shop_domain` helper plus the job templates that trust it) is the gem's own documented/generated code, so any app built from the generators without additional shop-binding checks is affected by default.

### Recommendation
- Do not derive tenant identity from the unsigned `X-Shopify-Shop-Domain` header for authorization purposes. Instead, parse and use the shop domain embedded in the verified webhook body payload (or bind the header into the HMAC-covered material) before trusting it for session/tenant lookup.
- Update `ShopifyApp::WebhookVerification#shop_domain` and all generator templates (`add_webhook`, `add_declarative_webhook`, `add_app_uninstalled_job`, `add_privacy_jobs`, `rotate_shopify_token_job`) to source the shop identity from data that is cryptographically bound to the verified request, and document clearly that the header alone must not be trusted as an authorization boundary (similar to the existing warning already given for `requested_shopify_domain`/`EnsureInstalled`).

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com`, subscribing to a webhook topic (e.g. `orders/create` or `app/uninstalled`).
2. Attacker triggers that event on their own store, capturing the resulting HTTP request Shopify sends to the app's webhook endpoint, including a valid `X-Shopify-Hmac-Sha256` header computed over the raw body with the app's shared secret.
3. Attacker replays this exact request to the same endpoint, keeping the body and HMAC header unchanged, but replacing `X-Shopify-Shop-Domain` with `victim-shop.myshopify.com`.
4. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which succeeds because the body/HMAC pair is untouched.
5. The controller (per documented pattern) enqueues the job with `shop_domain: "victim-shop.myshopify.com"`, and the job's `Shop.find_by(shopify_domain: shop_domain)` resolves to the victim's real `Shop` record, subsequently calling `shop.with_shopify_session` (or `shop.destroy` for the uninstall job) using the victim's stored access token - all triggered by the attacker's own request.

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L13-25)
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

**File:** lib/generators/shopify_app/add_app_uninstalled_job/templates/app_uninstalled_job.rb.tt (L1-20)
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
end
```
