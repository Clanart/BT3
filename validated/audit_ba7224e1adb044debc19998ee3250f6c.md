### Title
Webhook shop-domain header is not covered by HMAC verification, allowing cross-shop webhook forgery - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` authenticates the *body* of an inbound webhook with an HMAC over `request.raw_post`, but the `shop_domain` helper it exposes reads an unauthenticated request header (`HTTP_X_SHOPIFY_SHOP_DOMAIN`) that is never included in the signed material. This is the same class of bug as the "Imprecise Permissions" report: the permission/authenticity check protects one artifact (the payload) while a different, security-relevant attribute (which tenant the payload belongs to) is trusted without being covered by that check.

### Finding Description
`verify_request` computes `hmac_valid?(request.raw_post)` and only checks the raw POST body against `ShopifyApp.configuration.secret`/`old_secret`: [1](#0-0) 

The HMAC digest itself is computed strictly over `data` (the body), never mixing in any header: [2](#0-1) 

Yet the same concern exposes `shop_domain`, which simply returns the raw, attacker-controllable header value with no cross-check against the verified body or against any authenticated session: [3](#0-2) 

Critically, the gem's own documentation instructs integrators to use this unauthenticated `shop_domain` value directly as the tenant key for background job dispatch: [4](#0-3) 

The `ShopifyApp.configuration.secret` used for the HMAC is a single, app-wide client secret shared across every shop that has installed the app — it is not shop-specific. Because of that, *any* holder of a validly-signed webhook body (e.g., an attacker who has installed the app on their own development/test shop and thus receives real, correctly-HMAC-signed webhooks from Shopify) can replay that body to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header. `verify_request` will accept the request (HMAC over the body is untouched and still valid), and `shop_domain` will return the attacker-chosen victim domain to the application/job layer, exactly as the documented pattern does.

### Impact Explanation
Any controller/job built per the documented pattern (`SomeJob.perform_later(shop_domain: shop_domain, webhook: ...)`) will process attacker-supplied webhook content under an arbitrary, attacker-chosen `shop_domain`. Depending on how the job uses `shop_domain` (e.g., looking up a `Shop`/tenant record and writing associated data, or triggering shop-scoped side effects), this enables cross-shop data injection/corruption or a forged trust indicator attributed to a shop the attacker does not own — a cross-tenant integrity/authorization violation stemming directly from an accepted forged (mismatched) signed request.

### Likelihood Explanation
Exploitability requires the attacker to possess at least one legitimately HMAC-signed webhook body, which is trivial for any developer/merchant who can install the app on their own store (a normal, low-privilege, unrelated-merchant action) and capture a real webhook delivery. Replaying it with a modified domain header is a simple unauthenticated HTTP request; no secrets need to be leaked. Impact is amplified for every downstream implementation following the documented integration pattern verbatim.

### Recommendation
Bind the shop domain into the authenticity check instead of trusting an independent header:
- Verify that `shop_domain` corresponds to a shop known to have installed the app (e.g., cross-check against `ShopifyApp::SessionRepository` for that domain) before dispatching any job, and/or
- Prefer deriving the shop identity from the verified webhook payload/registry metadata (as `ShopifyAPI::Webhooks::Registry.process` does) rather than exposing a raw, unauthenticated `shop_domain` header accessor for direct use, and update the documented example to avoid trusting the header value without such a cross-check.

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com` and receives a legitimate webhook (e.g., `orders/create`) with a valid `X-Shopify-Hmac-Sha256` header computed by Shopify using the app's shared secret.
2. Attacker replays the identical raw body and valid HMAC header to the app's custom webhook endpoint, but sets `X-Shopify-Shop-Domain: victim.myshopify.com`.
3. `verify_request` in `lib/shopify_app/controller_concerns/webhook_verification.rb` calls `hmac_valid?(request.raw_post)`, which succeeds because the body/HMAC pair is untouched and valid.
4. The controller (built per `docs/shopify_app/webhooks.md`'s documented example) calls `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`, where `shop_domain` resolves to the attacker-forged `victim.myshopify.com`, causing the job to process attacker-supplied webhook data under the victim shop's identity.

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
