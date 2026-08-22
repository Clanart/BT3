### Title
Webhook shop-domain header is not covered by the HMAC signature, allowing a forged/replayed webhook to be attributed to a different shop - (File: lib/shopify_app/controller_concerns/webhook_verification.rb)

### Summary
`ShopifyApp::WebhookVerification` (and the underlying `ShopifyApp::PayloadVerification` it includes) authenticates incoming webhooks by validating an HMAC over the raw request body only. The `shop_domain` value, which apps are documented to use for attributing/routing the webhook to a specific shop, is read directly from the `X-Shopify-Shop-Domain` HTTP header and is never included in the HMAC digest or cross-checked against the signed payload.

### Finding Description
`hmac_valid?` computes and compares an HMAC over `request.raw_post` using the app's shared secret(s): [1](#0-0) 

`WebhookVerification#verify_request` only calls `hmac_valid?(data)` where `data = request.raw_post`, and separately exposes a `shop_domain` helper that simply reads the `X-Shopify-Shop-Domain` header — a value that is completely outside the scope of what the HMAC actually signs: [2](#0-1) 

The gem's own documentation instructs developers building custom webhook controllers to rely on this unauthenticated `shop_domain` value to route/attribute the webhook's job:
```ruby
def carts_update
  params.permit!
  SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
  head :no_content
end
``` [3](#0-2) 

Because the app-level HMAC secret is shared across every shop that installs the app (it's the app's client secret, not a per-shop secret), a merchant who installs the app on their own shop receives genuine `(body, X-Shopify-Hmac-Sha256)` pairs signed with that same shared secret. That merchant can then replay the exact same body+HMAC to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header for a victim shop's domain. `hmac_valid?` will still succeed because the header is not part of what's hashed, so the forged request is accepted as authentic — but the app processes it as if it originated from the victim shop.

This is the same root cause described in the reference report: an identifier used to determine "which entity this authenticated action applies to" (`permissionID` in the report, `shop_domain` here) is parsed independently of, and never bound to, the cryptographic verification that authenticates the payload. Any suitably-privileged/attacker-owned identity sharing the same signing material can be substituted in for the intended target identity.

### Impact Explanation
An attacker who has installed the app on their own shop (an "unrelated merchant") can craft webhook requests that the app will process as legitimately originating from a different, victim shop. Depending on how the consuming app uses the `shop_domain` helper in its custom webhook handlers (as directed by the gem's own documentation), this enables cross-shop data injection, spoofed events, or state changes attributed to a shop the attacker does not control — a forged signed request being accepted for the wrong tenant.

### Likelihood Explanation
This requires only that the attacker be able to install the target app on any shop they control (an "unrelated merchant" relative to the victim), which is normally low-friction/self-service for public apps. No secret leakage, host-operator access, or victim shop compromise is needed — only capturing one genuine webhook delivery to their own shop and replaying it with a modified header, which any HTTP client can do since the header is not authenticated.

### Recommendation
Bind the shop/topic identifiers into the authenticated verification step rather than trusting unsigned headers:
- Extend `hmac_valid?`/`verify_request` to require that the shop derivable from the verified payload (or from a value provided via a mechanism actually covered by the signature, e.g. Shopify's webhook headers as validated by the underlying webhook library) matches `X-Shopify-Shop-Domain` before exposing it via `shop_domain`.
- Alternatively, document and enforce that `shop_domain` must never be trusted as an authorization/tenant-scoping value on its own, and provide a verified alternative (e.g., derive shop from the already-authenticated session/webhook registry rather than a raw header) similar to how `TokenExchange#reject_mismatched_requested_shopify_domain` cross-checks requested vs. authenticated shop domains for session-based auth: [4](#0-3) 

### Proof of Concept
1. Attacker installs the target Shopify app on `attacker.myshopify.com`, triggering a legitimate webhook, e.g. `orders/create`, delivered with a valid `X-Shopify-Hmac-Sha256` (computed with the app's shared secret) and `X-Shopify-Shop-Domain: attacker.myshopify.com`.
2. Attacker captures the raw POST body and the accompanying HMAC header from this delivery.
3. Attacker replays this identical `(body, HMAC)` pair to the app's custom webhook endpoint, but sets `X-Shopify-Shop-Domain: victim.myshopify.com`.
4. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which passes because the body and HMAC are unchanged and were validly produced with the shared app secret: [5](#0-4) 
5. The custom controller (built per the gem's documented pattern) reads `shop_domain` and enqueues a job scoped to `victim.myshopify.com` using data that actually belongs to the attacker's shop, resulting in cross-shop data processing being accepted as if it came from the victim.

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

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L73-83)
```ruby
    def reject_mismatched_requested_shopify_domain
      requested_domain = requested_shopify_domain
      return false if requested_domain.blank?

      authenticated_domain = authenticated_shopify_domain_from_token
      return false if authenticated_domain.blank? || authenticated_domain == requested_domain

      ShopifyApp::Logger.debug("Shop context validation failed")
      head(:unauthorized)
      true
    end
```
