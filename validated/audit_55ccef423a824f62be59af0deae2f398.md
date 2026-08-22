### Title
Webhook Shop Domain Not Bound to HMAC Signature Enables Cross-Shop Webhook Forgery - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification` validates only the HMAC of the raw request body, while the shop identifier used for tenant routing (`shop_domain`) comes from a separate header that is never covered by that signature. This is the same class of bug reported in the JPEG'd finding: a privileged/trusted-looking action (here, "attribute this signed webhook to shop X") is performed without validating that the untrusted input (the domain header) actually corresponds to the value that was cryptographically authorized. Just as `CryptoPunksHelper` checked "is this call owner-authorized" but never checked "is the receiver value sane," `WebhookVerification` checks "is this body HMAC-valid" but never checks "does the domain header belong to the signed payload."

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` computes the HMAC exclusively over `request.raw_post`: [1](#0-0) 

`WebhookVerification#verify_request` gates the whole controller on this body-only check, but the module also exposes a `shop_domain` helper that reads a completely separate, unsigned header: [2](#0-1) 

The gem's own documentation instructs app developers to use exactly this pattern for custom webhook controllers, dispatching background jobs keyed by the unauthenticated `shop_domain` value alongside the (only body-verified) webhook payload: [3](#0-2) 

Because `X-Shopify-Shop-Domain` is not part of the signed material, any party capable of obtaining one genuine, HMAC-valid webhook delivery (e.g., an unrelated merchant who installs the app on their own store and receives a legitimate signed webhook for it) can replay that exact body+HMAC pair to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header value naming a different, victim shop. `hmac_valid?` still returns true (the body/signature pair is untouched and genuine), so `verify_request` passes, and the resulting job/record gets attributed to the victim shop chosen by the attacker.

### Impact Explanation
Any app built with the documented `WebhookVerification` + `shop_domain` pattern will process attacker-controlled webhook data under a shop identifier the attacker fully controls, independent of which shop actually generated the signed payload. Depending on what the app's webhook jobs do (e.g., updating shop-scoped settings, uninstall/redact handling, order or customer records keyed by shop), this allows cross-shop data injection or corruption without the attacker needing any credentials for the victim shop — only a single legitimate signed webhook of their own.

### Likelihood Explanation
Likelihood is high for apps following the gem's documented custom-webhook-controller pattern: obtaining one valid signed webhook only requires installing the app on any shop (including the attacker's own store), and HTTP headers are fully attacker-controlled on the replayed request since nothing but the body is authenticated.

### Recommendation
Do not derive the tenant/shop identifier from an unauthenticated header when only the body is HMAC-verified. Either:
- Include the shop domain (and other routing-critical headers) in the data covered by the HMAC computation before trusting it, or
- Rely solely on the shop identifier embedded in/derivable from the already-verified payload (as `ShopifyAPI::Webhooks::Registry.process`/`ShopifyAPI::Webhooks::Request` does internally), rather than exposing and documenting the standalone `shop_domain` header helper as a trusted value.

### Proof of Concept
1. Install the target app on an attacker-controlled shop `attacker.myshopify.com`; trigger any subscribed webhook topic to receive a genuine request from Shopify: body `B`, headers include `X-Shopify-Hmac-Sha256: H` (valid for `B`) and `X-Shopify-Shop-Domain: attacker.myshopify.com`.
2. Replay the captured request to the app's custom webhook endpoint (built per `docs/shopify_app/webhooks.md`'s example using `ShopifyApp::WebhookVerification`), keeping body `B` and header `H` unchanged, but replacing `X-Shopify-Shop-Domain` with `victim-shop.myshopify.com`.
3. `verify_request` calls `hmac_valid?(request.raw_post)`, which recomputes the HMAC over `B` using the app secret and compares to `H` — this still matches, so the before_action passes.
4. The controller/job (per the documented pattern) calls `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`, where `shop_domain` returns the attacker-supplied `"victim-shop.myshopify.com"`, causing the app to process/persist the attacker's data as if it originated from the victim shop.

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
