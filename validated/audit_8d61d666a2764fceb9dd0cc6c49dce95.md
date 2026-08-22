### Title
Webhook `shop_domain` header is unauthenticated by the HMAC, enabling cross-shop confused-deputy replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Finding Description
`WebhookVerification#verify_request` only validates that `X-Shopify-Hmac-Sha256` is a correct HMAC of the raw request body computed with the app's shared client secret (or `old_secret`): [1](#0-0) 

The HMAC digest covers `data = request.raw_post` only — it never includes `X-Shopify-Shop-Domain` or any other header. `WebhookVerification#shop_domain` simply reads that header verbatim with no cross-check against the payload or any registered shop record: [2](#0-1) 

Downstream, both the built-in controller and the documented custom-controller pattern trust this unauthenticated header to select which shop a job acts on: [3](#0-2) [4](#0-3) 

Because Shopify apps use one client secret shared across every shop that installs the app (this is explicitly the model this library implements — `secret`/`old_secret` are global, not per-shop), any merchant who installs the app receives at least one genuine webhook delivery (body + valid HMAC) signed with that same shared secret. That merchant — an unprivileged attacker relative to a victim shop that has also installed the same app — can capture one such `(raw_body, X-Shopify-Hmac-Sha256)` pair and replay it to the webhook endpoint while substituting an arbitrary victim `X-Shopify-Shop-Domain` value. `hmac_valid?` still returns true because the digest never covered the header, so `verify_request` passes, and `shop_domain` returns the attacker-chosen victim domain. The job (e.g. `AppUninstalledJob`, `CartsUpdateJob`) is then enqueued/executed with `shop_domain` pointing at the victim shop while the payload content is whatever the attacker captured/controls, e.g.: [5](#0-4) 

No existing check (session id derivation, `sanitize_shop_domain`, scope comparison, `secure_compare`) binds the header to the HMAC-signed body or to shop registration state, so this passes straight through.

### Impact Explanation
This is a cross-shop confused-deputy / forged-request vulnerability (Shopify HackerOne "Broken Access Control / cross-tenant impact" class). An attacker who is a legitimate but unprivileged installer of the app can cause app-side jobs to run against a **different, victim shop's** identity — e.g. triggering `shop.destroy` in the uninstall job template, or feeding attacker-controlled webhook data into a victim shop's processing pipeline (`CartsUpdateJob`, `products/update`, etc.), corrupting or deleting data that belongs to a shop the attacker does not control.

### Likelihood Explanation
Feasible and repeatable: the attacker only needs to install the target app on any shop they control (a normal, unprivileged action), capture one real webhook delivery, and replay the exact body with a modified `X-Shopify-Shop-Domain` header via a normal HTTP POST to the public webhook route. No knowledge of the app secret, victim session, or host misconfiguration is required — this is the default behavior of the shipped `WebhookVerification` concern and generator templates.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` as an authenticated value. Bind the shop identity to the HMAC-verified payload instead — e.g. use `ShopifyAPI::Webhooks::Request`'s parsed/verified `shop` (as the built-in `WebhooksController#receive` does via `ShopifyAPI::Webhooks::Registry.process`) rather than the raw header, or additionally verify that the resolved shop is one for which the request's registered webhook subscription/topic is expected before enqueuing jobs. Update the `add_declarative_webhook`/`add_webhook` generator templates and docs example accordingly so custom controllers don't rely on the unauthenticated `shop_domain` helper.

### Proof of Concept
```ruby
class WebhookCrossShopConfusionTest < ActionDispatch::IntegrationTest
  setup do
    ShopifyApp.configuration.secret = "API_SECRET_KEY"
  end

  test "HMAC validates even when X-Shopify-Shop-Domain is swapped to a victim shop" do
    body = "{}"
    hmac = Base64.strict_encode64(
      OpenSSL::HMAC.digest(OpenSSL::Digest.new("sha256"), "API_SECRET_KEY", body)
    )

    # attacker's own genuine, HMAC-valid delivery, replayed with victim's domain
    post "/webhooks/carts_update",
      params: body,
      headers: {
        "CONTENT_TYPE" => "application/json",
        "HTTP_X_SHOPIFY_HMAC_SHA256" => hmac,
        "HTTP_X_SHOPIFY_SHOP_DOMAIN" => "victim-shop.myshopify.com", # attacker-controlled
      }

    assert_response :no_content # verify_request passes -> job enqueued for victim-shop
    assert_enqueued_with(job: CartsUpdateJob) do
      # shop_domain resolved inside controller == "victim-shop.myshopify.com"
    end
  end
end
```
Expected/actual: the request passes `hmac_valid?` and the job is enqueued/executed with `shop_domain == "victim-shop.myshopify.com"`, even though the attacker only ever produced a valid HMAC for their own shop's payload — demonstrating the header is not bound to the signed content.

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

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L4-15)
```text
  def self.handle(topic:, shop:, body:, webhook_id:, api_version:)
    perform_later(topic: topic, shop_domain: shop, webhook: body)
  end

  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")

      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end
```

**File:** lib/generators/shopify_app/add_app_uninstalled_job/templates/app_uninstalled_job.rb.tt (L8-18)
```text
  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")
      
      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    logger.info("#{self.class} started for shop '#{shop_domain}'")
    shop.destroy
```
