Based on my investigation, I found a concrete analog to the reported pattern in `shopify_app`'s webhook verification flow.

### Title
Webhook `shop_domain` is not authenticated by the HMAC check, allowing cross-shop webhook spoofing - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` only validates the HMAC over the raw request body, but the `shop_domain` value that downstream code relies on to attribute/scope the webhook to a specific merchant is read directly from an unsigned HTTP header. This mirrors the `rngComplete` bug class: a value that determines *who* the privileged operation is attributed to (`_rewardRecipient` in the original finding, `shop_domain` here) is never covered by the same check that gates the "trusted" action, so it can be set independently by an unprivileged caller.

### Finding Description
`WebhookVerification#verify_request` computes and compares an HMAC over `request.raw_post` only: [1](#0-0) 

The `shop_domain` helper used by controllers (and documented as the tenant-scoping value passed into background jobs) simply reads a header that is not part of the HMAC-signed data: [2](#0-1) 

`hmac_valid?` itself only signs `data` (the raw body) with the shared secret — it never binds the shop/topic headers into the digest: [3](#0-2) 

The gem's own documentation instructs developers building custom webhook controllers to trust this unauthenticated `shop_domain` value for tenant attribution when enqueuing jobs: [4](#0-3) 

Because the merchant identity (`X-Shopify-Shop-Domain` header) is never covered by the signature, an attacker who owns a legitimate installed shop (or otherwise obtains one genuinely-signed webhook body+HMAC pair for their own store) can replay that exact same body/HMAC to the app's public webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header value. `verify_request` will still pass (`hmac_valid?` only checks the body), and `shop_domain` will report whatever the attacker put in the header — not the shop that actually produced the payload.

### Impact Explanation
Any code path that uses `shop_domain` from `WebhookVerification`/`PayloadVerification` (as explicitly recommended in the gem's own docs) to route data into a specific merchant's tenant context can be tricked into processing attacker-controlled webhook body content under a victim shop's identity, or vice versa — enqueuing jobs, writing records, or triggering side effects attributed to a shop that never sent that data. This is a cross-shop confusion/injection vector analogous to the `_rewardRecipient` bypass: the field that determines "who benefits/who owns this data" is decoupled from the actual authenticated/verified payload.

### Likelihood Explanation
Exploitation requires only that the attacker be able to obtain one genuinely HMAC-signed webhook body (trivial — install the app on their own free development store and trigger any subscribed event), then send a raw HTTP POST directly to the app's public webhook endpoint with a forged `X-Shopify-Shop-Domain` header. No secrets need to be leaked and no special privilege beyond having (or simulating) a shop installation is required, so likelihood is moderate-to-high wherever `shop_domain` is used for tenant scoping, which the gem explicitly documents as a supported pattern.

### Recommendation
Do not treat `shop_domain` (or any other unsigned header) as an authenticated value. Either:
- Include the shop domain/topic in the HMAC-covered payload check (Shopify's HMAC is only ever body-based, so this must be validated against the actual session/shop record the app has stored, not trusted as-is), or
- After verifying the HMAC, cross-check the header-derived `shop_domain` against a shop that the app has an active/authorized session/installation record for before using it to scope any write, job enqueue, or lookup, rejecting the request if there's no matching authorized shop.

### Proof of Concept
1. Attacker installs the target app on their own store `attacker-shop.myshopify.com` and triggers a webhook event (e.g., `carts/update`), capturing the exact raw POST body and the valid `X-Shopify-Hmac-Sha256` header Shopify sent.
2. Attacker sends a direct HTTP POST to the app's webhook endpoint (e.g. `/webhooks/carts_update`) reusing the identical body and valid HMAC header, but replaces `X-Shopify-Shop-Domain` with `victim-shop.myshopify.com`.
3. `WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which succeeds since the body/HMAC pair is genuinely valid.
4. The controller (per the gem's documented pattern) calls `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`, enqueuing the job with `shop_domain == "victim-shop.myshopify.com"` even though the actual webhook content originated from the attacker's own shop.

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

**File:** docs/shopify_app/webhooks.md (L88-104)
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
```
