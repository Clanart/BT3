Based on the report's bug class (an unauthenticated/unchecked caller-supplied identity being trusted for a privileged operation, where the underlying "signature"/verification doesn't actually cover the identity value being trusted), the closest concrete analog in `shopify_app` is in webhook HMAC verification.

### Title
Webhook HMAC verification does not sign the shop-attribution header, allowing forged cross-shop webhook attribution - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` validates a webhook's `X-Shopify-Hmac-Sha256` header against the HMAC of the request body only, but the shop attribution value used by app code (`shop_domain`) is taken from a separate, unsigned header. Any request whose body+HMAC pair is valid for the shared app secret will be accepted regardless of which shop's domain header accompanies it, letting an attacker who possesses one valid (body, HMAC) pair from their own shop re-attribute that payload to a different, unrelated shop.

### Finding Description
`verify_request` computes `hmac_valid?` using only `request.raw_post`: [1](#0-0) 
and `WebhookVerification#shop_domain` simply reads the caller-supplied `X-Shopify-Shop-Domain` header, which is never included in the HMAC computation: [2](#0-1) 

Because the HMAC secret for a given app is the same across all shops that install that app (it is the app's client secret, not a per-shop secret), any shop that has legitimately installed the app can receive a genuinely Shopify-signed webhook (valid body + HMAC pair) for its own store, then replay that exact body/HMAC pair to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header with a different, victim shop's domain. `verify_request` still passes because it only checks the body against the HMAC, and downstream code trusts `shop_domain` (or the equivalent header-derived shop from `ShopifyAPI::Webhooks::Request`) as the tenant/shop the payload belongs to: [3](#0-2) 

This is documented as the intended integration pattern for custom controllers, explicitly using the unverified `shop_domain` to key jobs per shop: [4](#0-3) 

This mirrors the reported bug class: a value (`feeController`/shop identity) that determines whose "account"/data an operation is attributed to is accepted from an unchecked source even though a cryptographic check exists elsewhere in the flow — the check simply doesn't cover the value being trusted.

### Impact Explanation
An attacker controlling one shop's installation of the app can forge the shop attribution of webhook-driven side effects (job data, order/cart/product records, any stored data keyed by `shop_domain`) for a different, unrelated shop that also has the app installed. This is a cross-shop data injection/poisoning primitive: the app processes and stores attacker-supplied payload content under another merchant's identity, e.g. `CartsUpdateJob.perform_later(shop_domain: attacker_chosen_shop, webhook: attacker_controlled_body)`.

### Likelihood Explanation
Requires the attacker to install the target app on at least one shop they control (a common, low-barrier action available to any merchant/attacker for public apps), and knowledge of a real target shop domain (public information, e.g., `shopname.myshopify.com`). No secret material needs to be leaked because the attacker's own shop already receives validly HMAC-signed webhooks.

### Recommendation
Bind the shop domain to the signed payload, or derive shop attribution strictly from a value that Shopify includes inside the HMAC-covered body/topic, rather than an independent, unsigned header. At minimum, cross-check that the shop referenced by the header actually installed the app and has a matching stored session before using `shop_domain` for any authorization or data-attribution decision, and document that `shop_domain` must not be trusted as an authenticated tenant identifier on its own.

### Proof of Concept
1. Install the target app on attacker-controlled shop `attacker.myshopify.com`; capture a legitimate webhook request (body `B`, header `X-Shopify-Hmac-Sha256: H`, header `X-Shopify-Shop-Domain: attacker.myshopify.com`) that Shopify sends to the app's webhook endpoint.
2. Resend the exact same request to the app's webhook endpoint, keeping body `B` and HMAC header `H` unchanged, but replacing `X-Shopify-Shop-Domain` with `victim.myshopify.com`.
3. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which only checks `B` against `H` — this still validates successfully. [5](#0-4) 
4. The app's controller/job (per the documented pattern) uses `shop_domain` (now `victim.myshopify.com`) to process/store the attacker-controlled body `B` as though it were `victim`'s data.

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
