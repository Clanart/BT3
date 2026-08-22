### Title
Webhook shop-domain/topic spoofing via HMAC scope mismatch enables cross-shop replay attacks - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification` (and the underlying `PayloadVerification`) only computes and checks the HMAC signature over the **request body** (`request.raw_post`). The `shop_domain` (and topic) values that downstream webhook jobs use to decide *which shop's data to mutate* are read from HTTP **headers**, which are never included in the HMAC digest. This is the same class of bug as the HatsSignerGate issue: one security check (the signature) validates a subset of the data, while a different, unverified subset of the same request (the header-derived shop context) is what's actually used to make the authorization-relevant decision.

### Finding Description
`hmac_valid?` in `PayloadVerification` computes `OpenSSL::HMAC.digest(digest, secret, data)` where `data = request.raw_post`, and compares it to the `X-Shopify-Hmac-Sha256` header. [1](#0-0) 

`WebhookVerification#verify_request` only checks `hmac_valid?(request.raw_post)`, and separately exposes `shop_domain` sourced directly from the `X-Shopify-Shop-Domain` header — a value that is not part of the signed payload at all: [2](#0-1) 

`WebhooksController#receive` forwards `request.headers.to_h` (unfiltered, unverified) into `ShopifyAPI::Webhooks::Request`, which extracts topic/shop from those headers for dispatch: [3](#0-2) 

Every generated webhook job template then trusts this header-derived `shop_domain` unconditionally to look up and mutate the corresponding `Shop` record — including destroying it in the mandatory `app/uninstalled` handler: [4](#0-3) [5](#0-4) 

The integration test confirms the HMAC is computed purely over the body while `x-shopify-shop-domain` and `x-shopify-topic` are separate, independently-controllable headers: [6](#0-5) 

Because the app's HMAC secret is a single shared client secret (not per-shop), any party who has legitimately received one real webhook from Shopify for their own installed shop (e.g. by triggering `app/uninstalled`, `shop/redact`, or any registered topic on their own store) possesses a valid `(body, HMAC)` pair. Since headers are outside the signed scope, they can replay the exact same body and HMAC while substituting the `X-Shopify-Shop-Domain` (and/or `X-Shopify-Topic`) header with a victim shop's domain that is also installed on the app. `hmac_valid?` still passes because it never inspects headers, and the downstream job then acts on the attacker-chosen `shop_domain`.

### Impact Explanation
An attacker (any merchant/user who has installed the app, i.e., an "unrelated-merchant" with no privileges over the victim shop) can forge webhook deliveries attributed to a different shop, causing the app's webhook jobs to execute cross-shop:
- Replaying a captured `app/uninstalled` webhook against a victim's `shop_domain` header causes `Shop.find_by(shopify_domain: shop_domain).destroy` to run against the victim's shop record — a cross-shop account/session deletion (denial of service).
- Replaying other captured webhook topics can be used to trigger arbitrary registered handlers against a victim shop, since the "shop" and "topic" the job trusts are entirely header-controlled and unverified.

This matches "cross-shop access" impact criteria: an unrelated, unprivileged merchant can force an app to treat a forged/replayed request as authoritative for a shop they do not own.

### Likelihood Explanation
Likelihood is real but requires the attacker to capture at least one legitimately signed webhook body+HMAC pair for their own shop (trivially achievable — anyone can install the app, trigger a webhook such as uninstalling the app, and capture the raw request via a proxy), plus knowledge of the victim's `myshopify.com` domain (public information, or discoverable via public storefronts / app reviews). No secret material or privileged access is required beyond being an app user.

### Recommendation
Bind the shop/topic context cryptographically to the signature verification, mirroring the H-3 fix philosophy of "check the same data everywhere":
- Include the shop domain and topic (or the full raw headers used for dispatch) as part of the HMAC-verified payload before trusting them, or
- Re-derive/verify the shop domain from an authenticated source (e.g., cross-check against a per-shop webhook secret, or verify the shop is a currently-installed shop with an active session before acting), and reject requests where the header-derived shop cannot be independently corroborated.
- At minimum, webhook job templates should not perform destructive actions (`shop.destroy`) purely based on unauthenticated header data without additional idempotency/replay protection (e.g., checking `webhook_id` uniqueness against previously processed IDs scoped to the shop that actually holds an active session/token).

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com` and captures a legitimately delivered webhook (e.g. `app/uninstalled`), recording the raw POST body and the `X-Shopify-Hmac-Sha256` header value Shopify sent.
2. Attacker replays the exact same HTTP request to the app's `/webhooks/...` endpoint, but changes only the `X-Shopify-Shop-Domain` header to `victim.myshopify.com` (and, if needed, `X-Shopify-Topic`).
3. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which passes because the body is unchanged.
4. `WebhooksController#receive` forwards the (attacker-modified) headers to `ShopifyAPI::Webhooks::Registry.process`, which dispatches to the app's `AppUninstalledJob` with `shop: "victim.myshopify.com"`.
5. `AppUninstalledJob#perform` looks up `Shop.find_by(shopify_domain: "victim.myshopify.com")` and calls `shop.destroy`, deleting the victim's install record despite the victim never uninstalling the app.

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

**File:** test/integration/webhooks_controller_test.rb (L48-60)
```ruby
    def headers(name)
      hmac = OpenSSL::HMAC.digest(
        OpenSSL::Digest.new("sha256"),
        "API_SECRET_KEY",
        "{}",
      )
      headers = {
        "x-shopify-topic" => name,
        "x-shopify-hmac-sha256" => Base64.encode64(hmac),
        "x-shopify-shop-domain" => "test.myshopify.com",
      }
      headers
    end
```
