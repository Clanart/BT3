### Title
Webhook shop-domain is trusted directly from unauthenticated header, allowing cross-shop webhook spoofing/replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb](lib/shopify_app/controller_concerns/webhook_verification.rb))

### Summary
`ShopifyApp::WebhookVerification#shop_domain` returns the raw, attacker-controlled `X-Shopify-Shop-Domain` header value without any cryptographic binding to the HMAC-verified body, and `hmac_valid?` only authenticates the request *body* against a single app-wide shared secret, not the shop identity. Since the same secret is used for every shop's webhooks, a valid `(body, HMAC)` pair legitimately obtained for one shop (e.g. the attacker's own installed shop) remains valid when replayed with the `X-Shopify-Shop-Domain` header changed to an arbitrary victim shop, and downstream jobs then act on that spoofed shop identity.

### Finding Description
`verify_request` computes `hmac_valid?(request.raw_post)` by iterating the app's shared `secret`/`old_secret` and comparing against `X-Shopify-Hmac-Sha256`: [1](#0-0) 
This computation depends only on `data` (the raw body) and the shared secret — the shop domain header is never part of the signed material. `shop_domain` then simply reads the unauthenticated header: [2](#0-1) 

Because the same secret authenticates webhooks for *all* shops of the app (there is no per-shop signing key derived from a verified session, ID token, or shop record), an attacker who controls their own legitimately-installed shop can capture a genuine `(raw body, HMAC)` pair issued by Shopify for their own webhook, then POST that exact body/HMAC to the app's webhook endpoint while substituting `X-Shopify-Shop-Domain` with a victim shop's domain. `hmac_valid?` still returns `true` because the check never inspects the header, so `verify_request` passes.

Downstream consumers — the built-in `ShopifyApp::WebhooksController#receive` (`ShopifyAPI::Webhooks::Registry.process`), and generated job templates such as `AppUninstalledJob`/`ShopRedactJob` — use the `shop`/`shop_domain` value handed to them by the request/registry pipeline to look up and act on a local `Shop` record, e.g.: [3](#0-2) 
and the documented custom-controller pattern explicitly passes the unauthenticated `shop_domain` straight into a job: [4](#0-3) 
None of `sanitize_shop_domain`, session lookup, or scope comparison is applied to `shop_domain` in this concern — the only gate is `hmac_valid?`, which does not bind body to shop.

### Impact Explanation
An attacker who has legitimately installed the app on their own shop can trigger a genuine webhook (e.g. `app/uninstalled`, `shop/redact`, or any subscribed topic) to obtain a valid `(body, HMAC)` pair, then replay it with a forged `X-Shopify-Shop-Domain` targeting an arbitrary victim shop. Depending on which job the app wires up, this can cause: destructive action against another shop's local record (`shop.destroy` in the `AppUninstalledJob` template), triggering shop-redact/data-request processing for a shop the attacker doesn't control, or generally cross-shop confusion where webhook data is attributed to and processed under an arbitrary shop identity chosen by the attacker. This matches Shopify's "cross-shop data/action confusion via forged signed request" impact class — the signature check gives a false sense of authenticity binding the payload to the asserted shop.

### Likelihood Explanation
Preconditions are minimal and within the unprivileged attacker model: the attacker only needs to install the app on any shop they control (a normal, permitted action) to receive at least one genuine webhook with a valid HMAC, then can replay that exact body+HMAC combination with an arbitrary shop-domain header indefinitely (HMAC has no timestamp/nonce/shop binding, so replay is unlimited and the exploit is fully repeatable).

### Recommendation
Never trust `X-Shopify-Shop-Domain` as an authenticated claim. At minimum: (1) verify the asserted shop domain against a known/installed `Shop` record with an active session/token before processing, and cross-check it matches shop-specific webhook registration metadata (e.g. via `ShopifyAPI::Webhooks::Registry`'s handler mapping which ties `shop` to the delivery, not the raw header) rather than the app-level `WebhookVerification#shop_domain`; (2) where per-shop secrets are configurable, use per-shop HMAC verification so the signature itself is bound to the shop; (3) treat `shop_domain` from this concern as an unverified hint only, and require jobs to independently validate that the shop is currently installed with a matching webhook delivery before performing destructive/scoped actions.

### Proof of Concept
```ruby
# test/integration/webhooks_controller_forgery_test.rb
require_relative "../test_helper"

class WebhookCrossShopForgeryTest < ActionDispatch::IntegrationTest
  test "hmac valid for shop A body is accepted with header claiming shop B" do
    body = "{}"
    hmac = Base64.encode64(
      OpenSSL::HMAC.digest(OpenSSL::Digest.new("sha256"), "API_SECRET_KEY", body)
    )

    # This body/HMAC pair was genuinely issued by Shopify for "shop-a.myshopify.com",
    # but the header is forged to claim it belongs to "victim-shop.myshopify.com".
    post shopify_app.webhooks_path("order_update"),
      params: body,
      headers: {
        "x-shopify-topic" => "order_update",
        "x-shopify-hmac-sha256" => hmac,
        "x-shopify-shop-domain" => "victim-shop.myshopify.com", # attacker-chosen, not shop-a
      },
      as: :json

    # verify_request passes (HMAC check succeeds) even though the header
    # does not correspond to the shop that actually owns this secret-signed payload.
    assert_response :ok
    # downstream job/controller receives shop_domain == "victim-shop.myshopify.com"
    # with no verification that this webhook actually originated from that shop.
  end
end
```
Expected (buggy) result: request returns `200 OK`, demonstrating `hmac_valid?` accepts the payload regardless of the asserted shop, and `shop_domain` propagates the unverified, attacker-chosen value to any job/controller relying on it.

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
