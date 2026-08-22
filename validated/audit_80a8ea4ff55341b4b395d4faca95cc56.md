### Title
Webhook shop identity is derived from an unsigned HTTP header, enabling cross-shop webhook forgery - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` only computes/verifies the HMAC over the raw POST body of an incoming webhook [1](#0-0) , but it also exposes a `shop_domain` accessor that simply reads the unsigned `X-Shopify-Shop-Domain` request header [2](#0-1) . This is the exact analog of the reported bug class: a value that is *not covered by the integrity check* (the C4 report's user-supplied `d` address that bypasses proper validation of the redeem target) is trusted downstream to determine which entity ("shop") the verified payload is attributed to, without binding that value to the signed content.

### Finding Description
The `verify_request` before_action only validates `request.raw_post` against the shared app secret using HMAC-SHA256 [3](#0-2) . The HMAC signature never covers HTTP headers, only the raw body. Yet `shop_domain` — the method the gem's own documentation instructs developers to use for dispatching per-shop background jobs — pulls its value directly from the `X-Shopify-Shop-Domain` header [2](#0-1) :

```ruby
def shop_domain
  request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]
end
```

The official docs recommend exactly this pattern for any custom webhook controller built with this concern:
```ruby
SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
``` [4](#0-3) 

Because a given app's client secret is shared across every merchant that installs the app, any merchant (an "unrelated" install) can obtain a validly-HMAC-signed `(body, hmac)` pair for their own store's events (e.g. by triggering a `carts/update` on their own shop) and then replay that exact `(body, hmac)` pair directly to the app's public webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header value pointing at a *different, victim* shop. `verify_request` will still pass because the HMAC check only depends on the body and shared secret, not on the header [1](#0-0) . The resulting job is then queued and executed with the attacker-chosen `shop_domain`, e.g. `Shop.find_by(shopify_domain: shop_domain)` as shown in the generated privacy-webhook job template pattern [5](#0-4) , causing the attacker's own webhook payload to be processed and persisted under the victim shop's identity/session.

### Impact Explanation
This is a cross-shop confusion vulnerability: an unrelated, authenticated (but low-privilege) merchant can forge webhook events that the app attributes to a shop they do not own, because the value used to select the target shop (`shop_domain`) is never bound to the HMAC-verified payload. Depending on how the consuming job uses `shop_domain` (which the gem's own docs and generators encourage keying database writes, session lookups, or privacy actions on), this can lead to writing/overwriting another merchant's data, or triggering shop-scoped operations (e.g. mandatory privacy jobs such as `shop/redact`) against the wrong store — a cross-shop access/data-integrity issue analogous to the reported redeem bug where an unvalidated, attacker-influenced identifier is trusted to select which resource state gets mutated after an otherwise-valid check.

### Likelihood Explanation
Exploitation requires only: (1) installing the app as an ordinary, unprivileged merchant (which is the normal, expected flow for any Shopify app), (2) capturing one legitimately signed webhook body+HMAC pair from your own store, and (3) POSTing it to the app's public `/webhooks/:type` endpoint with a forged `X-Shopify-Shop-Domain` header. No secrets need to be leaked and no privileged access is required — this is directly reachable from an anonymous/unrelated-merchant HTTP request to the app's own webhook endpoint.

### Recommendation
Do not derive shop identity from an unsigned header. Instead, extract the shop identity from data that is itself covered by the HMAC-verified body (e.g. the `shop_id`/`shop_domain` fields already present inside most webhook payloads, or via the `ShopifyAPI::Webhooks::Request`/`Registry.process` flow used by the built-in `WebhooksController`, which resolves shop context from registered webhook handling rather than a raw header). At minimum, the `shop_domain` helper and its documented usage pattern should be removed or clearly marked as unsafe unless independently cross-checked against a value included in the signed payload.

### Proof of Concept
1. Install the target app on an attacker-controlled development store; trigger any subscribed webhook (e.g. `carts/update`) to capture a legitimate `(raw_body, X-Shopify-Hmac-Sha256)` pair signed with the app's shared secret.
2. Send a POST request directly to the app's custom webhook route (built using `ShopifyApp::WebhookVerification`, per the documented pattern) reusing that exact `raw_body` and `X-Shopify-Hmac-Sha256` header, but set `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
3. `verify_request` calls `hmac_valid?(request.raw_post)` [1](#0-0)  which succeeds because it only checks the body/secret.
4. The controller action calls `shop_domain`, returning the attacker-forged `victim-shop.myshopify.com` [2](#0-1) , and enqueues the job with that shop domain, causing the attacker's payload to be processed under the victim's shop context.

### Citations

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L15-21)
```ruby
    def verify_request
      data = request.raw_post
      unless hmac_valid?(data)
        ShopifyApp::Logger.debug("Webhook verification failed - HMAC invalid")
        head(:unauthorized)
      end
    end
```

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L23-25)
```ruby
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
