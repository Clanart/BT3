### Title
Webhook HMAC verification does not bind the payload to the `X-Shopify-Shop-Domain` header, enabling cross-shop webhook forgery - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
This mirrors the reported `MultiMerkleDistributor` bug class: a cryptographically verified blob (the merkle proof / here, the webhook HMAC) is accepted without binding it to the context it is meant to be scoped to (`questID`/`period` there, `shop_domain` here). Any party that can obtain one validly-signed webhook body for the shared app secret can replay that exact body against the app's webhook endpoint while supplying an arbitrary `X-Shopify-Shop-Domain` header, and the verification concern will accept it as authentic for that other shop.

### Finding Description
`ShopifyApp::WebhookVerification#verify_request` only computes the HMAC over `request.raw_post` and never incorporates the shop-identifying header into the signed material: [1](#0-0) 

The underlying `hmac_valid?` helper in `PayloadVerification` compares the request's `HTTP_X_SHOPIFY_HMAC_SHA256` header against an HMAC of `data` (the raw body) keyed by the app's shared secret/old secret — again with no shop binding: [2](#0-1) 

The `shop_domain` value used downstream to attribute the webhook to a specific tenant is read directly from the (unsigned, non-HMAC-covered) `X-Shopify-Shop-Domain` header: [3](#0-2) 

This value is passed straight into the background job that performs shop-scoped writes, as shown in the generator-produced controller template that every app using declarative webhooks ships with: [4](#0-3) 

and in the docs' recommended custom-controller pattern: [5](#0-4) 

Because the app's webhook secret (`ShopifyApp.configuration.secret`) is a single value shared across **all shops that install the app** (it is the app's client secret, not a per-shop secret), any merchant who installs the app can trigger a legitimate webhook for their own shop and obtain a validly-signed raw body + HMAC pair. That signed body is not bound to a shop identifier the way the merkle-tree fix bound `questID`/`period` into the leaf hash. An attacker can therefore replay the exact same signed body to the app's webhook endpoint while substituting a different value in `X-Shopify-Shop-Domain`. `hmac_valid?` still returns `true` because it only checks the body, and `shop_domain` returns the attacker-controlled header value, so the app's job layer processes attacker-supplied webhook content as if it originated from a completely unrelated victim shop.

### Impact Explanation
This is a direct analog of "accepted forged signed request" / cross-shop data injection: an unrelated, unprivileged merchant can cause the app to enqueue and process webhook jobs (e.g., `OrdersCreateJob`, `ProductsUpdateJob`, or any app-specific webhook job) tagged with a victim shop's domain but containing attacker-chosen data. Depending on what the job does with `shop_domain` + `webhook` payload (common patterns: loading the `Shop` record for that domain and writing/mutating tenant data, sending notifications, updating inventory/pricing), this can corrupt another merchant's data or trigger unintended side effects under their tenant context — the same "wrong rewards claimed under the wrong quest/period" pattern, translated to "wrong data written under the wrong shop."

### Likelihood Explanation
Likelihood is low-to-medium: it requires the attacker to (a) be a merchant with the target app installed on their own store (to generate a validly HMAC-signed body using the shared secret) and (b) know or guess the victim's `myshopify.com` domain (which is often discoverable/public). No compromise of the app's secret or victim credentials is needed, and the request path (webhook endpoint) is exposed to any HTTP caller who can produce a matching HMAC — matching the report's framing of "likelihood low, impact high → medium risk."

### Recommendation
Bind the shop context into the verified material, analogous to adding `questID`/`period` into the merkle leaf hash: compute/validate the HMAC over a canonical combination of the raw body *and* the `X-Shopify-Shop-Domain` (and ideally `X-Shopify-Webhook-Id`/topic) header, or independently verify that the shop domain in the header matches a shop record that is expected to be sending this specific webhook (e.g., cross-check against `ShopifyApp::SessionRepository` before dispatching the job). At minimum, downstream job consumers should not treat the header-derived `shop_domain` as trusted tenant-scoping data without an additional authenticated linkage to the signed payload.

### Proof of Concept
1. Attacker installs the target Shopify app on their own store `attacker-shop.myshopify.com`.
2. Attacker triggers a real event (e.g., updates a product) causing Shopify to send a webhook to the app's endpoint with a valid `X-Shopify-Hmac-Sha256` (computed with the app's shared secret) and body `B`.
3. Attacker captures `B` and the valid HMAC value, then sends their own POST request directly to the app's webhook endpoint:
   - Headers: `X-Shopify-Hmac-Sha256: <captured-valid-hmac>`, `X-Shopify-Shop-Domain: victim-shop.myshopify.com`, `X-Shopify-Topic: products/update`
   - Body: `B` (unchanged)
4. `ShopifyApp::WebhookVerification#verify_request` calls `hmac_valid?(request.raw_post)`, which succeeds because the body `B` matches what was HMAC'd.
5. `shop_domain` resolves to `"victim-shop.myshopify.com"` from the forged header.
6. The generated job (e.g., `ProductsUpdateJob.perform_later(shop_domain: "victim-shop.myshopify.com", webhook: B)`) runs, processing attacker-controlled content as if it belongs to `victim-shop.myshopify.com`.

### Citations

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L15-25)
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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L7-10)
```text
    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
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
