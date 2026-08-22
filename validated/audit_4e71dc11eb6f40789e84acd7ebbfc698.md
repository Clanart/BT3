### Title
Webhook HMAC verification does not bind the signed payload to the claimed shop domain, allowing cross-shop webhook payload forgery - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` authenticates a webhook by checking only that the raw request body's HMAC matches a digest computed with the app's shared secret. The `shop_domain` value used to route the payload to a specific merchant's job is read straight from the unauthenticated `X-Shopify-Shop-Domain` header, which is never included in the HMAC computation. This mirrors the H-15 bug class: a signature is validated, but ownership of the claimed identity (there, the inferer's pubkey; here, the shop that "owns" the payload) is never checked.

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` computes `OpenSSL::HMAC.digest(digest, secret, data)` over `request.raw_post` only: [1](#0-0) 

`ShopifyApp::WebhookVerification#verify_request` uses this same check as its sole gate before letting a webhook through: [2](#0-1) 

Crucially, the module also exposes `shop_domain`, which is read directly off an attacker-controllable HTTP header and is never part of the HMAC-signed material: [3](#0-2) 

The documented and generator-produced usage pattern for custom webhook controllers dispatches background jobs keyed off this unauthenticated `shop_domain`: [4](#0-3) [5](#0-4) 

Because the webhook secret (`ShopifyApp.configuration.secret`) is a single app-level secret shared across every installed shop — not a per-shop key — any merchant who has installed the app can legitimately trigger an event on their own store and obtain a genuinely-signed `(body, HMAC)` pair for that body content (e.g., via Shopify's webhook test/redelivery UI, or by triggering the event and capturing the delivery). Since the shop attribution header is outside the signed data, that exact `(body, HMAC)` pair can be replayed by directly POSTing to the app's public webhook endpoint with the `X-Shopify-Shop-Domain` header swapped to a victim shop's domain. `verify_request` will still pass because the body/HMAC pair is valid for the shared secret, and the resulting job will be enqueued attributing the attacker's payload to the victim shop — exactly analogous to the original report where a valid signature was accepted without checking that the signing key belonged to the claimed data owner.

### Impact Explanation
An attacker (any merchant who installs the app) can forge webhook deliveries attributed to a different, victim shop by replaying self-obtained signed bodies with a modified shop-domain header. Depending on what the app does with webhook data (e.g., updating billing state, inventory, customer records, GDPR/redact jobs, order processing), this enables cross-shop data injection/corruption using a request that the endpoint accepts as authentic — matching the "accepted forged signed request" / "cross-shop access" impact class.

### Likelihood Explanation
Exploitability requires only: (1) being a merchant who can install the app and trigger an ordinary webhook-generating action on their own shop, and (2) being able to observe the exact delivered body and its `X-Shopify-Hmac-Sha256` value (available via Shopify's built-in webhook delivery/test tooling), and (3) sending a direct HTTP POST to the app's public webhook endpoint with a forged `X-Shopify-Shop-Domain` header. No secret knowledge or privileged access is needed, making this reachable from an unrelated-merchant (non-victim) actor.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` (or any other header) as an unverified identity claim. Instead, ensure the shop identity is either embedded in and covered by the signed payload, or independently cross-checked against a stored session/shop record after verifying the HMAC — reject the webhook if the claimed shop cannot be corroborated against the session store (`ShopifyApp::SessionRepository`) or if the shop parsed from the verified payload disagrees with the header. Also consider deriving the shop_domain in a canonical, verifiable way (e.g., cross-referencing against known installed shops before dispatching any job).

### Proof of Concept
1. Attacker installs the target app on their own store `attacker-shop.myshopify.com`.
2. Attacker triggers an event that fires the registered webhook (e.g., updates a product), and captures the delivered request body `B` and its `X-Shopify-Hmac-Sha256` header value `H` (visible via Shopify's webhook delivery logs/redelivery feature in the Partner/Admin dashboard).
3. Attacker sends a direct POST to the victim app's public webhook endpoint (e.g., `POST /webhooks/receive`) with:
   - Body: `B` (unchanged)
   - Header `X-Shopify-Hmac-Sha256: H` (unchanged)
   - Header `X-Shopify-Shop-Domain: victim-shop.myshopify.com` (forged)
4. `ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(B)`, which passes because the shared app secret matches `H` for body `B` — the shop-domain header was never part of the check: [2](#0-1) 
5. The controller (custom controller pattern or generated job dispatch) enqueues the job using `shop_domain` = `victim-shop.myshopify.com`, processing attacker-controlled webhook content as if it originated from the victim shop.

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

**File:** docs/shopify_app/webhooks.md (L88-96)
```markdown
```ruby
class CustomWebhooksController < ApplicationController
  include ShopifyApp::WebhookVerification

  def carts_update
    params.permit!
    SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
    head :no_content
  end
```

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L7-10)
```text
    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
```
