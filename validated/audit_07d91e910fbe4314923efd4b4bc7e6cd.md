### Title
Webhook HMAC Verification Does Not Bind Shop Domain or Topic, Allowing Cross-Shop Webhook Replay/Forgery - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification#verify_request` only validates that the HMAC-SHA256 digest of the raw request body matches a digest computed with the app's shared client secret. Neither the `X-Shopify-Shop-Domain` header nor the `X-Shopify-Topic` header is included in the signed material, yet both are trusted downstream to determine which shop the webhook applies to and which job/handler processes it. This mirrors the reported bug class: the signature does not bind to the specific "identity" (here, the shop) it is meant to authorize, so a signature/body pair that is legitimately generated for one context can be replayed under a different, attacker-chosen identity.

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` computes/verifies the HMAC solely over `request.raw_post`: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` uses only this body-based check as the before_action gate: [2](#0-1) 

Immediately after, the module exposes `shop_domain`, which is read directly from an unauthenticated, attacker-controlled header that was never covered by the HMAC: [3](#0-2) 

The documented, supported way to build a custom webhook controller relies on exactly this trust: the shop domain (and by extension the webhook's topic, taken from `X-Shopify-Topic`) is used to key background jobs, but it is never checked to be part of what Shopify actually signed: [4](#0-3) 

Because `ShopifyApp.configuration.secret` (the single app client secret) is shared across every shop the app is installed on, any attacker who installs the app on their own store will receive genuine, validly-signed webhooks for that store. The HMAC signature only proves "this body was signed with our app secret" — it proves nothing about *which* shop or *which* topic it belongs to, since those values live in separate headers outside the signed payload.

### Impact Explanation
An attacker who has installed the target app on a shop they control (a normal, unprivileged, unrelated-merchant action) can capture a legitimately-signed webhook body+HMAC pair from Shopify, then replay it against the same app's webhook endpoint while substituting `X-Shopify-Shop-Domain` (and/or `X-Shopify-Topic`) with a victim shop's domain. `verify_request` still passes because it never inspects those headers, so the app enqueues jobs (e.g. order/product/webhook handlers, or even `app/uninstalled`) attributing the attacker-controlled body to the victim shop. This is a forged, accepted signed request that causes cross-shop data corruption/griefing (e.g., injecting fabricated "order" or "product" payloads into a victim shop's records, or falsely tripping uninstall-cleanup logic for a shop that never actually uninstalled), without the victim's authorization — the same class of harm the reported analog describes (a valid signature being replayed against a target it wasn't intended for because the signed payload lacks a binding identifier).

### Likelihood Explanation
Likelihood is limited by two factors: (1) the attacker needs to actually install the target app (or otherwise obtain a genuinely-signed webhook body/HMAC pair), which is possible for anyone since app installation itself is normally open to any merchant/developer account; and (2) the attacker cannot arbitrarily choose body *content*, only whichever body Shopify happened to sign for their own store's events — but topic and shop domain headers are fully attacker-controlled and unchecked, so they can freely relabel a captured payload as belonging to any shop and (depending on how the consuming job interprets `topic`) any handler. This is reachable from an anonymous/unrelated-merchant HTTP request path (webhook endpoint), matching the required threat model.

### Recommendation
Include the shop domain (and ideally the topic) as part of the material that is authenticated for each webhook request, rather than trusting the `X-Shopify-Shop-Domain` / `X-Shopify-Topic` headers independently of the HMAC. Concretely: verify that the `X-Shopify-Shop-Domain` header corresponds to a shop that has an active session/install record before processing, and/or use per-shop secrets or an authenticated binding of shop+topic+body (e.g., by validating that the webhook's payload internally references the same shop, or delegating fully to `ShopifyAPI::Webhooks::Registry`'s own verified `Request` object rather than re-deriving `shop_domain` from a raw header in custom controllers). At minimum, document and enforce that consumers of `WebhookVerification#shop_domain` must not treat it as authenticated data on its own.

### Proof of Concept
1. Attacker installs the target Shopify app on their own store `attacker-shop.myshopify.com`, obtaining a genuine `orders/create` webhook: body `B`, header `X-Shopify-Hmac-Sha256: H` where `H = HMAC_SHA256(secret, B)`.
2. Attacker sends `POST /webhooks/orders_create` (or whichever custom webhook route uses `ShopifyApp::WebhookVerification`) to the app with:
   - Body: `B` (unchanged, so `H` remains valid)
   - Header `X-Shopify-Hmac-Sha256: H`
   - Header `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (forged)
   - Header `X-Shopify-Topic: <any topic recognized by app>`
3. `verify_request` (`lib/shopify_app/controller_concerns/webhook_verification.rb:15-21`) only checks `hmac_valid?(B)`, which succeeds since `B`/`H` were genuinely produced by Shopify.
4. The controller proceeds to process the webhook using `shop_domain` = `"victim-shop.myshopify.com"`, queuing a job/job payload that the app attributes to the victim shop, even though the victim shop never sent or authorized this event.

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
