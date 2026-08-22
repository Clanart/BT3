### Title
Webhook shop-domain header is not covered by HMAC verification, allowing cross-shop impersonation of an otherwise-valid signed webhook - (File: lib/shopify_app/controller_concerns/webhook_verification.rb)

### Summary
`ShopifyApp::WebhookVerification#verify_request` authenticates only the raw request body via HMAC-SHA256, while `shop_domain` — the value used everywhere in the gem's webhook pipeline to select the tenant/shop — is read straight from the unauthenticated `X-Shopify-Shop-Domain` header, with no binding between the two. This is the same root-cause pattern as the reported analog: a value that is supposed to be intrinsically tied to a verified artifact (tokenId encoding plotSize / here, shop identity encoded in the signed webhook) is instead accepted from a separate, independently-controllable parameter that is never cross-checked against the verified data.

### Finding Description
`verify_request` computes/validates HMAC over `request.raw_post` only: [1](#0-0) 

`hmac_valid?` in `PayloadVerification` confirms the body was signed with the app's client secret, but the secret is shared across *every shop* that installs the app — it is not a per-shop secret: [2](#0-1) 

The `shop_domain` method used for tenant selection reads directly from the `HTTP_X_SHOPIFY_SHOP_DOMAIN` header, which is not part of the HMAC digest: [3](#0-2) 

This unauthenticated `shop_domain` value is exactly what the gem's own generated webhook jobs use to resolve the tenant/`Shop` record and activate a Shopify session for that shop: [4](#0-3) 

and the documented custom-controller pattern does the same: [5](#0-4) 

Because the app secret is identical for all shops, any merchant that has installed the app can capture a legitimately signed webhook sent to their own store (valid body + valid HMAC), then resubmit the exact same raw body/HMAC pair to the app's webhook endpoint while substituting the `X-Shopify-Shop-Domain` header for a different (victim) shop's domain. `verify_request` will still accept it because the HMAC only covers the body, and downstream code that trusts `shop_domain` (as the gem's own generators/docs instruct) will process the replayed payload under the wrong shop's `Shop` record and Shopify session.

### Impact Explanation
Any code built on top of `ShopifyApp::WebhookVerification` that uses `shop_domain` (the header) to select the shop record and open `shop.with_shopify_session` — which is exactly the pattern the gem's own generators produce — can be tricked into executing webhook-triggered business logic in the context of a shop the attacker does not own. This is a cross-shop/cross-tenant integrity issue: attacker-controlled webhook data can be applied against a victim shop's session/records, since the signature never binds the claimed shop identity to the payload.

### Likelihood Explanation
Exploitation requires only that the attacker be a legitimate merchant of the app (an "unrelated merchant" from the target's perspective), capable of receiving a real webhook to their own store and replaying it with a different domain header — no secret leakage or code execution needed. Because the client secret is shared across all installs, this is reachable from any anonymous/unrelated-merchant relationship to the victim, matching the same trust-mismatch pattern in the external report (verified value vs. unverified, attacker-supplied parameter that is expected to match it).

### Recommendation
Bind the shop identity into the verified payload instead of trusting the header independently: verify that `shop_domain` from the header matches a shop identity contained in (or otherwise derivable only from) the HMAC-covered body, or use `ShopifyAPI::Webhooks::Registry.process`/`Request#shop` consistently and reject requests where the header-derived shop cannot be corroborated. At minimum, document and enforce that consumers must not treat the unauthenticated `shop_domain` header as an authorization/tenant-scoping value without additional verification, mirroring the recommended fix in the analog report (verify that the trusted-looking field actually corresponds to the verified data before acting on it).

### Proof of Concept
1. Attacker installs the app on `attacker.myshopify.com` and receives a legitimate webhook: body `B`, header `X-Shopify-Shop-Domain: attacker.myshopify.com`, valid `X-Shopify-Hmac-Sha256` computed over `B` with the shared app secret.
2. Attacker resends the identical request to the app's webhook endpoint, changing only `X-Shopify-Shop-Domain` to `victim.myshopify.com`.
3. `verify_request` recomputes HMAC over `B` (unchanged) and accepts the request: [6](#0-5) 
4. The webhook job (per the generator template) looks up `Shop.find_by(shopify_domain: "victim.myshopify.com")` and runs `shop.with_shopify_session` using the attacker's payload: [7](#0-6) 
5. Business logic in the job now executes with `victim`'s shop/session context using attacker-controlled webhook data, despite a "valid" HMAC.

### Citations

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

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L8-20)
```text
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
