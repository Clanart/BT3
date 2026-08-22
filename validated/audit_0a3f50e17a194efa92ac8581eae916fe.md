### Title
Webhook `shop_domain` is derived from an unverified `X-Shopify-Shop-Domain` header while HMAC validation only covers the request body, enabling cross-shop webhook attribution forgery - (File: lib/shopify_app/controller_concerns/webhook_verification.rb)

### Summary
`ShopifyApp::WebhookVerification#verify_request` validates a webhook request by checking the HMAC of `request.raw_post` only, never validating that the `X-Shopify-Shop-Domain` header actually belongs to the entity that produced the signed body. The same module then exposes a `shop_domain` helper that simply echoes that unverified header, and the gem's own documentation instructs consumers to key downstream processing (job dispatch, tenant lookup) off that value. This mirrors the Shelter.sol root cause: an unverified, attacker-influenced identifier (`_to`/header) is trusted for state-changing/tenant-scoping purposes instead of the value tied to the verified/authenticated source (`msg.sender`/HMAC-signed payload).

### Finding Description
The verification logic is: [1](#0-0) 

`hmac_valid?` only signs/validates `data = request.raw_post`: [2](#0-1) 

The `X-Shopify-Shop-Domain` header is never part of the HMAC-signed material — it is read independently as a plain header value: [3](#0-2) 

The gem's documented pattern for custom webhook controllers explicitly uses this unverified `shop_domain` for shop-scoped dispatch: [4](#0-3) 

Because the app's HMAC secret (`ShopifyApp.configuration.secret`) is a single shared app-level secret used across every shop that installs the app (not per-shop), any merchant/tenant that has legitimately installed the app can obtain a validly-signed webhook body+HMAC pair for their own shop (via genuine Shopify webhook deliveries), and then replay that exact body/HMAC to the app's custom webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header value pointing at a different, victim shop domain. `verify_request` will pass (the body/HMAC pair is valid), and `shop_domain` will resolve to the attacker-chosen value, exactly analogous to Shelter.sol trusting `_to` instead of `msg.sender`.

### Impact Explanation
Any code following the documented integration pattern (`SomeJob.perform_later(shop_domain: shop_domain, webhook: ...)`) will process/attribute webhook data under a forged shop domain. Depending on the consuming job's logic (e.g., privacy/redact jobs, `Shop.find_by(shopify_domain: shop_domain)` followed by destructive or data-returning operations, as seen in the generator templates), this enables cross-shop data corruption, unauthorized data deletion, or leakage of another shop's data/state to an unrelated, lower-privileged party — a concrete cross-shop access issue reachable by any existing app-installer, not just developers or host operators.

### Likelihood Explanation
Likelihood is elevated because: (1) the attacker only needs to be a legitimate but unrelated merchant who has installed the app — no special privilege is required; (2) the HMAC secret is shared across all shops for a given app, so a validly-signed body is trivially obtainable by capturing the attacker's own real webhook traffic; (3) the vulnerable pattern (`shop_domain` header trust) is the officially documented way to implement custom webhook controllers in this gem, meaning it is very likely present in real downstream apps that follow the docs.

### Recommendation
Do not derive shop identity from the raw `X-Shopify-Shop-Domain` header. Instead, bind shop identity to the verified payload: either include the shop domain in the HMAC-signed computation, cross-check the header against a shop value embedded in the verified webhook body, or use `ShopifyAPI::Webhooks::Request`/`Registry.process` (which associates verified webhook context with topic/shop from the signed request pipeline) instead of exposing a bare unauthenticated `shop_domain` helper. At minimum, update `WebhookVerification#shop_domain` and its documentation to make clear it must not be trusted for tenant scoping without additional verification, mirroring how `_to` should have been replaced with `msg.sender` in the original Shelter.sol fix.

### Proof of Concept
1. Install the vulnerable app on Shop A (attacker-controlled) and capture a genuine, valid webhook delivery (raw body + `X-Shopify-Hmac-Sha256` header) sent by Shopify to the app's custom webhook endpoint, e.g. `carts_update`.
2. Replay that exact body and HMAC header to the same endpoint, but change the `X-Shopify-Shop-Domain` header to `victim-shop.myshopify.com`.
3. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)` — validation passes because the body/HMAC pair is untouched.
4. The controller calls `shop_domain`, which returns the attacker-supplied `victim-shop.myshopify.com`, and dispatches `SomeJob.perform_later(shop_domain: "victim-shop.myshopify.com", webhook: attacker_controlled_payload)` per the documented pattern (`docs/shopify_app/webhooks.md` lines 86-104) — the job now runs attacker-controlled webhook data attributed to a victim shop it does not belong to.

### Citations

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
