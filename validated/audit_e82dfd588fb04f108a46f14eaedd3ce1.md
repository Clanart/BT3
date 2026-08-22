### Title
Webhook Shop-Domain Header Not Covered by HMAC Allows Cross-Shop Webhook Spoofing - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` only authenticates the **raw request body** against the HMAC signature. The shop context (`X-Shopify-Shop-Domain` header) that downstream app code uses to attribute and process the webhook is read directly from an unauthenticated header and is never covered by that HMAC check, so it can be freely modified by anyone able to produce one validly-signed webhook body.

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` computes the HMAC over `request.raw_post` only: [1](#0-0) 

`WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` and, if it matches, allows the request through — it never checks the `shop_domain` header against anything: [2](#0-1) 

The `shop_domain` helper simply returns the raw, attacker-influenceable header value: [3](#0-2) 

The gem's own documented/generated pattern trusts this value as the tenant/shop identifier passed straight into background jobs that write app data, with no secondary validation that the shop actually matches the signed payload's issuer: [4](#0-3) [5](#0-4) 

This mirrors the Treasury `deposit()` bug class: an operation that blindly trusts caller-supplied, unauthenticated input (`token` address in the report; `shop_domain` header here) as a key/state selector, letting an outside party corrupt state that should be scoped to a different, legitimate party (a specific token balance in the report; a specific shop's data here).

### Impact Explanation
Since the HMAC secret is the app's single shared secret (not shop-specific), any party who can legitimately trigger one webhook delivery for their own installed shop obtains a validly-signed `(body, HMAC)` pair. They can then replay that exact body/HMAC pair to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header for an arbitrary victim shop domain. Because `shop_domain` is passed unchecked into `perform_later(shop_domain: shop_domain, ...)`, the app's job processes/persists that payload as if it originated from the victim shop — enabling cross-shop data injection/corruption (e.g., creating, updating, or deleting records keyed by shop, similar to the Treasury `_totalValue` corruption disrupting the legitimate owner's operations).

### Likelihood Explanation
Requires only an "unrelated merchant" who has (or can create) a shop with the app installed and can trigger any subscribed webhook topic (e.g., `carts/update`), which is normal, self-service merchant activity — no privileged access or leaked secrets needed. The header is trivially forgeable in the replayed HTTP request.

### Recommendation
- Bind the shop domain into the HMAC-verified data (e.g., verify the HMAC over body **and** shop header, or use the shop returned by `ShopifyAPI::Webhooks::Request`'s own signature-verified parsing rather than a raw header lookup used independently for app logic).
- Cross-check the `shop_domain` used for persistence against a known/installed shop record (e.g., only accept webhooks for shops present in `SessionRepository`) before dispatching jobs.
- Document/enforce that consumers must not trust `shop_domain` from `WebhookVerification` without this additional check.

### Proof of Concept
1. Attacker installs the app on `attacker.myshopify.com` and triggers a subscribed webhook topic (e.g. updates a cart), receiving a Shopify-originated POST with a valid `X-Shopify-Hmac-Sha256` for that body.
2. Attacker replays the identical raw body and HMAC header to the app's webhook endpoint, but changes `X-Shopify-Shop-Domain` to `victim.myshopify.com`.
3. `verify_request` validates the HMAC against the (unchanged) body and passes the request; `shop_domain` returns `"victim.myshopify.com"`.
4. The app's job (per the documented pattern) is invoked with `shop_domain: "victim.myshopify.com"`, causing data intended for the attacker's own shop to be written/processed under the victim shop's context.

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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L7-10)
```text
    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
```
