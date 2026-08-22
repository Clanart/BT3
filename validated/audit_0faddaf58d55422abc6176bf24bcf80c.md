### Title
Webhook shop-domain attribution trusts an unauthenticated header instead of the HMAC-verified payload - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` authenticates a webhook request by computing an HMAC over the raw POST body only, but exposes a separate `shop_domain` helper that reads the `X-Shopify-Shop-Domain` HTTP header — a value that is **not** covered by the HMAC signature. Any custom webhook controller built on top of this concern (as documented) uses this unauthenticated header value to attribute the incoming webhook to a shop record, mirroring the reported bug class: trusting an externally supplied "bookkeeping" value instead of verifying it against the cryptographically authenticated data.

### Finding Description
`hmac_valid?` in `PayloadVerification` computes: [1](#0-0) 

and `WebhookVerification#verify_request` only feeds `request.raw_post` into this check: [2](#0-1) 

The module also defines a `shop_domain` accessor used to identify which shop the webhook belongs to: [3](#0-2) 

This header is read directly from `request.headers` and is **never included in the HMAC computation**, so its authenticity is never checked — only the raw body bytes are validated against the shared app secret. The gem's own documentation instructs consumers to build custom webhook controllers that pass this unauthenticated value straight into job/queue attribution logic: [4](#0-3) 

Because the HMAC secret (`ShopifyApp.configuration.secret`) is shared across all shops/tenants of the app (it is the app's client secret, not a per-shop secret), any merchant that has installed the app can receive genuine, correctly-signed webhooks for their own shop. Since the `X-Shopify-Shop-Domain` header sits outside the signed payload, that merchant can replay the same signed body while substituting the header value with an arbitrary target shop domain, and `verify_request` will still report the request as HMAC-valid. Any downstream code that trusts `shop_domain` (as the docs recommend) to select or scope a Shop/tenant record will then act on incorrect bookkeeping — the same root cause as the referenced report: internal state (here, "which shop does this data belong to") is inferred from unauthenticated claimed input rather than being derived from/verified against the authenticated source of truth.

### Impact Explanation
An attacker who legitimately installs the app on their own shop can forge the shop attribution of an otherwise validly-signed webhook to point at a victim shop. Any consumer app that follows the documented pattern (queueing a job keyed by `shop_domain`) is exposed to cross-shop data confusion/injection — e.g., writing or overwriting data belonging to a shop the attacker does not control, using a payload the attacker fully crafted for their own shop. This is a cross-tenant integrity issue reachable by any unrelated/anonymous-relative merchant, matching the "cross-shop access" acceptance criterion.

### Likelihood Explanation
Exploitation only requires an attacker to (1) install the app on a shop they control (a normal, unprivileged action any merchant can take) and (2) capture/replay one legitimate outbound webhook while modifying the `X-Shopify-Shop-Domain` header, which is not defended by the gem. No secret leakage or special privilege is required beyond being an ordinary app user, and the gem's documented pattern actively encourages using the unauthenticated header for attribution.

### Recommendation
Do not treat `X-Shopify-Shop-Domain` as trusted merely because `hmac_valid?` returned true. Either:
- Include the shop domain header (or webhook topic/ID) as part of the HMAC-covered material, or
- Re-derive the shop from the signed payload body itself (most Shopify webhook payloads already carry shop-identifying data), or
- Cross-check the header value against a shop domain associated with the specific webhook subscription/session before using it for tenant-scoped writes.

### Proof of Concept
1. Attacker installs the app on `attacker.myshopify.com`; the app registers a webhook (e.g., `orders/create`).
2. Shopify sends the attacker's endpoint a webhook: body `B`, headers include `X-Shopify-Hmac-Sha256: HMAC(secret, B)` and `X-Shopify-Shop-Domain: attacker.myshopify.com`.
3. `WebhookVerification#verify_request` calls `hmac_valid?(B)`, which succeeds because the signature matches the body `B` (the header is irrelevant to this check): [2](#0-1) 
4. Attacker resends the identical request to the app's webhook endpoint but with the header changed to `X-Shopify-Shop-Domain: victim.myshopify.com`.
5. `verify_request` still passes (HMAC over `B` is unchanged), and any handler using `shop_domain` (per the documented pattern) processes/persists the attacker-controlled body `B` under `victim.myshopify.com`'s tenant scope.

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
