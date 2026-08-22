### Title
Webhook HMAC verification does not bind the signed payload to the `X-Shopify-Shop-Domain` header, allowing cross-shop webhook replay - (File: lib/shopify_app/controller_concerns/webhook_verification.rb)

### Summary
`ShopifyApp::WebhookVerification` only proves that a request body was signed with the app's shared secret; it never verifies that the `shop_domain` reported by the request actually corresponds to the shop that produced that signed body. Because the HMAC secret is shared by every shop that installs the app, an attacker who owns one installation can capture a legitimately-signed webhook and replay it with the `X-Shopify-Shop-Domain` header swapped to point at a victim shop. The request is accepted as authentic, and app code that trusts `shop_domain` to select the shop/session (the documented pattern) will then process the attacker's payload using the victim's access token.

### Finding Description
`verify_request` only checks the HMAC of the raw body against the shared app secret: [1](#0-0) 

The HMAC computation itself never includes or binds to the shop domain: [2](#0-1) 

`shop_domain` is derived purely from an attacker-controllable request header, with no cryptographic tie to the signature: [3](#0-2) 

The gem's own documentation instructs app authors to trust this unauthenticated `shop_domain`/`webhook_request.shop` value to look up the `Shop` record and open a session with that shop's stored access token, exactly the "shared authority, no check that the request actually belongs to that account" pattern described in the report: [4](#0-3) [5](#0-4) 

Because `ShopifyApp.configuration.secret` (and `old_secret`) is a single value shared across every shop that has the app installed, any shop owner can obtain a validly-signed webhook body/HMAC pair for their own shop (e.g., by triggering a webhook topic on their own store) and then resend that exact body+HMAC to the app's webhook endpoint with the `X-Shopify-Shop-Domain` header changed to any other shop that has the app installed. `hmac_valid?` will still return `true` because it only checks the body against the shared secret — it has no knowledge of, and does not validate, which shop the header claims to be from.

### Impact Explanation
An unauthenticated/unrelated merchant (any shop that has the app installed) can forge a request that is accepted as an authentic webhook "from" a different, victim shop. Any app built on the documented pattern (`Shop.find_by(shopify_domain: shop_domain)` then `shop.with_shopify_session`) will execute the attacker-controlled webhook body in the context of the victim shop's stored offline access token — enabling cross-shop data injection/processing using another merchant's credentials, directly analogous to the referenced report's cross-user withdrawal (a validly "signed" message is accepted and executed against the wrong account because there is no binding check that the acting identity matches the message's declared owner).

### Likelihood Explanation
Requires the attacker to control at least one shop where the app is installed (any merchant can install most public Shopify apps) and to know the victim's `myshopify.com` domain (often discoverable/guessable or public). No secret leak, dev/host access, or dependency bug is required — only the standard, documented flow of the in-scope `shopify_app` webhook concern and job templates.

### Recommendation
Bind the trusted shop identity to the verified payload instead of an unauthenticated header: derive `shop_domain` only from data that is itself covered by the HMAC (or from a value looked up via a shop-specific secret/session, not the app-wide shared secret), and reject requests where the header-declared shop cannot be cryptographically tied to the verified body. At minimum, document that `shop_domain` from `WebhookVersification` must never be used as the sole tenant selector without an additional shop-specific integrity check.

### Proof of Concept
1. Attacker installs the app on `attacker-shop.myshopify.com` and triggers any subscribed webhook topic (e.g., updates a product), causing Shopify to POST a signed payload to the app's webhook endpoint with headers `X-Shopify-Hmac-Sha256: <valid-hmac>` and `X-Shopify-Shop-Domain: attacker-shop.myshopify.com`.
2. Attacker captures the raw request body and the valid HMAC header.
3. Attacker resends the identical body and HMAC header to the same endpoint, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
4. `verify_request` (`lib/shopify_app/controller_concerns/webhook_verification.rb`) calls `hmac_valid?(request.raw_post)`, which succeeds because the body/HMAC pair is legitimate and the secret is shared across all shops.
5. The controller/job reads `shop_domain` from the (forged) header, loads `victim-shop`'s `Shop` record, and processes the attacker's payload using the victim shop's access token via `shop.with_shopify_session`.

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
