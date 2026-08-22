## Title
Webhook shop-domain header is not covered by HMAC verification, enabling cross-shop webhook forgery - (`lib/shopify_app/controller_concerns/webhook_verification.rb`)

## Summary
`ShopifyApp::WebhookVerification` validates only the raw request body against the app's shared HMAC secret, but the `shop_domain` helper it exposes reads an attacker-controllable HTTP header that is never included in that signature. Because the app-level HMAC secret is identical for every shop that installs the app, any merchant can obtain a genuinely Shopify-signed webhook for their own shop and replay it with a modified `X-Shopify-Shop-Domain` header, causing the app to process attacker-supplied webhook data as if it belonged to a different, victim shop — the same "privileged capability used outside its intended, narrower trust boundary" pattern described in the source report (a mechanism that proves one thing, `HMAC validity`, being trusted for something broader, `shop identity`).

## Finding Description
`hmac_valid?` computes/compares an HMAC over `request.raw_post` using `ShopifyApp.configuration.secret` (a single, app-wide secret, not shop-specific): [1](#0-0) 

`WebhookVerification#verify_request` only calls `hmac_valid?(request.raw_post)` before allowing the action to proceed, and separately exposes `shop_domain`, which is read directly from the `X-Shopify-Shop-Domain` header with no cryptographic binding to the verified body: [2](#0-1) 

The gem's own documentation instructs developers to trust this header-derived value as the shop identifier for background job processing: [3](#0-2) 

Because the HMAC secret is shared across all shops using the app (it is a single value from `ShopifyApp.configuration.secret`, not a per-shop secret), a valid, Shopify-signed HMAC only proves the request body was produced by Shopify for *some* shop with this app installed — it says nothing about which shop. Any actor who can install the app on their own shop (an unprivileged, unrelated-merchant action) can trigger a real event, capture the genuinely signed webhook, and resend it with the `X-Shopify-Shop-Domain` header rewritten to a victim shop's domain. `verify_request` still passes because the header isn't part of the signed payload.

## Impact Explanation
Any controller following the documented `ShopifyApp::WebhookVerification` pattern (as shown in `docs/shopify_app/webhooks.md`) will accept forged, cross-shop-attributed webhook data: jobs are enqueued with `shop_domain: shop_domain` where `shop_domain` is unauthenticated attacker input. Depending on what the app does with that value (e.g., writing order/customer data, triggering redaction/uninstall workflows, or other shop-scoped side effects keyed by `shop_domain`), this allows an unrelated merchant to inject or corrupt data attributed to a shop they do not control — a cross-shop write/impersonation via an accepted forged signed request.

## Likelihood Explanation
Likelihood is realistic: the attacker only needs to install the target app on any store (their own), which is an ordinary, unprivileged, unrelated-merchant action, then capture and replay one of their own legitimately signed webhooks with an edited header. No secret leakage or developer error is required beyond following the gem's documented usage pattern for custom webhook controllers.

## Recommendation
Bind the trusted shop identity to the verified request instead of an unsigned header:
- Derive the shop from the signed webhook body payload (most Shopify webhook payloads include shop-scoped identifiers), or use per-shop verification/session lookup rather than the raw `X-Shopify-Shop-Domain` header.
- If the header must be used, include it in the HMAC-covered data, or independently verify it against a shop known to have generated the signed body (e.g., cross-check against `ShopifyAPI::Webhooks::Request`'s parsed shop field where the gem's registry already extracts and validates topic/shop from the verified payload) instead of documenting the raw header as trustworthy.

## Proof of Concept
1. Attacker installs the target Shopify app on their own store (`attacker.myshopify.com`) — an ordinary, unprivileged merchant action.
2. Attacker triggers a webhook event (e.g., updates a product) and captures the resulting POST from Shopify, including a valid `X-Shopify-Hmac-Sha256` header computed from the app's shared secret over the raw body.
3. Attacker replays the exact same body/HMAC to the app's custom webhook endpoint (built per `docs/shopify_app/webhooks.md`'s recommended pattern using `ShopifyApp::WebhookVerification`), but changes the `X-Shopify-Shop-Domain` header to `victim.myshopify.com`.
4. `verify_request` calls `hmac_valid?(request.raw_post)` [4](#0-3)  which succeeds because the body/HMAC pair is genuinely valid (just for a different shop).
5. The controller calls `shop_domain`, which returns the attacker-controlled header value `victim.myshopify.com` [5](#0-4) , and enqueues a job with that value as the trusted shop, per the documented pattern [6](#0-5) .

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L13-25)
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
