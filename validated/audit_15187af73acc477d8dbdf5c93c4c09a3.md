### Title
Webhook `shop_domain` identity is not bound to the HMAC-verified payload, enabling cross-shop webhook spoofing — (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification#verify_request` authenticates an incoming webhook solely by recomputing an HMAC over `request.raw_post` and comparing it to the `X-Shopify-Hmac-Sha256` header [1](#0-0) . The shop identity used by application code, however, is read from a completely separate, unsigned header via `shop_domain` [2](#0-1) . Because `hmac_valid?` only signs `data = request.raw_post` [3](#0-2) , the `X-Shopify-Shop-Domain` header is never part of what is authenticated. This is the same class of bug as the reported issue: one code path (the HMAC check) verifies one piece of data, while a different code path (`shop_domain`, used for tenant attribution) trusts an unrelated, unauthenticated value as if it had been verified together with it.

### Finding Description
The documented pattern for building a custom webhook consumer is:
```ruby
class CustomWebhooksController < ApplicationController
  include ShopifyApp::WebhookVerification

  def carts_update
    params.permit!
    SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
    head :no_content
  end
end
``` [4](#0-3) 

`WebhookVerification` wires `verify_request` as a `before_action` and exposes `shop_domain`: [5](#0-4) 

The HMAC secret (`ShopifyApp.configuration.secret`) is a single, app-wide client secret shared by every shop that installs the app — it is not per-shop [3](#0-2) . Since the signature only covers `request.raw_post`, any request whose body produces a valid HMAC will pass `verify_request`, regardless of the `X-Shopify-Shop-Domain` header value sent alongside it. A merchant who has legitimately installed the app (an "unrelated merchant" with respect to any other victim shop) will receive genuine Shopify webhooks for their own store, each with a body and a matching valid HMAC. Nothing prevents that merchant from replaying the same `(body, hmac)` pair to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header (e.g., a victim shop's domain). The request still satisfies `hmac_valid?`, so `verify_request` accepts it, and `shop_domain` returns the attacker-chosen value, causing the attacker's payload to be processed as though it belongs to the victim shop.

### Impact Explanation
Any app built on the documented `WebhookVerification` pattern (including the mandatory GDPR/privacy webhook jobs and any custom webhook consumer using `shop_domain`) can be made to attribute attacker-controlled webhook content to an arbitrary other shop. Depending on what the downstream job does with `shop_domain` (e.g., updating a `Shop`/tenant record, triggering shop-scoped side effects, cache invalidation, billing/usage events, or writing data keyed by shop), this enables cross-shop data corruption or cross-tenant effects triggered by a merchant who has no relationship to the victim shop — a direct violation of tenant isolation.

### Likelihood Explanation
Exploitation requires only that the attacker be a legitimate, unprivileged merchant who has installed the app once (to receive at least one authentic webhook with a valid HMAC for some body content they control, e.g., by updating a cart/product on their own store to trigger `carts/update`). They do not need the app's secret, developer access, or any privileged role — only the ability to replay an HTTP POST with a modified header, which is straightforward. This is a realistic "unrelated-merchant" attack path reachable purely over HTTP.

### Recommendation
Bind the trusted shop identity to the verified payload instead of trusting a separate header:
- Prefer deriving the shop from the signed webhook body/topic metadata that the `shopify_api` gem's `ShopifyAPI::Webhooks::Request`/`Registry.process` already parses and verifies together, rather than exposing a standalone unauthenticated `shop_domain` helper.
- If `shop_domain` must remain, cross-check the header value against an active, known session/installation record for that shop (e.g., via `SessionRepository`) before allowing it to be used for any tenant-scoped side effects, and document clearly that `shop_domain` is untrusted unless additionally validated.
- Consider including the shop domain in the HMAC computation (or otherwise cryptographically binding header and body) if the message format can be extended, so verification actually authenticates the tenant, not just the payload.

### Proof of Concept
1. Attacker installs the target app on their own shop, `attacker.myshopify.com`, and configures a custom webhook consumer following the documented pattern:
```ruby
class CustomWebhooksController < ApplicationController
  include ShopifyApp::WebhookVerification
  def carts_update
    params.permit!
    SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
    head :no_content
  end
end
```
2. Attacker triggers a `carts/update` event on their own store, capturing the genuine webhook request Shopify sends: body `B` and header `X-Shopify-Hmac-Sha256: H` (valid because `H = HMAC_SHA256(secret, B)` and `secret` is the same for all shops).
3. Attacker resends the exact same request to the app's webhook endpoint, but replaces the `X-Shopify-Shop-Domain` header with `victim-shop.myshopify.com`, keeping body `B` and HMAC header `H` unchanged.
4. `WebhookVerification#verify_request` calls `hmac_valid?(B)`, which succeeds because it never inspects the shop-domain header [1](#0-0) .
5. `shop_domain` returns `"victim-shop.myshopify.com"` [2](#0-1) , and `SomeJob` is enqueued attributing the attacker's cart content `B` to `victim-shop.myshopify.com`, despite the attacker having no relationship to that shop.

### Citations

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L1-27)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module WebhookVerification
    extend ActiveSupport::Concern
    include ShopifyApp::PayloadVerification

    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_request
    end

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
