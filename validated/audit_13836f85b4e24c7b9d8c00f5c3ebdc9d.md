### Title
Webhook `shop_domain` value is trusted without being covered by HMAC verification, enabling cross-shop data injection - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#verify_request` only validates the HMAC over the raw request body, never over the `X-Shopify-Shop-Domain` header. The `shop_domain` helper method (and the equivalent `webhook_request.shop` used in the gem's own generator templates) reads that unauthenticated header directly and hands it to downstream jobs, which key stored/processed data by shop. This mirrors the reported bug class: an untrusted, unvalidated value (analogous to `blockHead.hash` from an untrusted node) is concatenated with/trusted alongside data that has actually been cryptographically verified, letting an attacker inject a different "identity" into an otherwise-signed payload.

### Finding Description
`hmac_valid?` computes the signature over `request.raw_post` only: [1](#0-0) 

`WebhookVerification#verify_request` calls this and, on success, allows the request to reach the action. Separately, `shop_domain` is derived straight from a request header that participates in no cryptographic check at all: [2](#0-1) 

The gem's own documented pattern for custom webhook controllers explicitly trusts this unauthenticated value to route data by shop: [3](#0-2) 

and the declarative-webhook generator template does the same via `webhook_request.shop`: [4](#0-3) 

Because the HMAC only protects `raw_post` (the body), the `X-Shopify-Shop-Domain` header can be modified in transit (e.g., by a malicious/compromised intermediary, proxy, or via request replay/forwarding) without invalidating the signature check — exactly like the original report where an untrusted party injects data (there: `blockHead.hash`; here: the shop-domain header) that rides along with, but is not covered by, the trusted/verified data (there: the signed operation bytes; here: the HMAC-verified body).

### Impact Explanation
If a webhook payload with a valid HMAC for shop A is delivered/forwarded with the `X-Shopify-Shop-Domain` header pointing to shop B (a different merchant), any app built following the gem's documented pattern will process/store that body under shop B's identity — leading to cross-shop data confusion, incorrect job execution (e.g., privacy webhooks like `customers_redact` or `shop_redact` acting on the wrong shop), or state corruption keyed by an attacker-controlled shop value. This is a cross-shop integrity issue reachable purely over the public webhook HTTP endpoint.

### Likelihood Explanation
Exploitation requires the attacker to be able to intercept/replay/forward a legitimate Shopify-signed webhook body while altering only the shop-domain header — feasible for any party positioned between Shopify and the app (compromised proxy/CDN, logging relay, or an attacker able to replay captured webhook traffic to the public endpoint), since the header carries no cryptographic binding to the signed body. It does not require knowledge of the app's webhook secret.

### Recommendation
- Short term: Stop trusting `X-Shopify-Shop-Domain` (or `webhook_request.shop`) as an authoritative value. Extract the shop from a field inside the HMAC-covered body (or otherwise cryptographically bind header-to-body, e.g. by including the shop domain in the signed payload check) before using it to key any downstream processing/storage.
- Long term: Treat all headers and side-channel metadata from Shopify webhook requests as untrusted unless explicitly covered by the HMAC, and document/enforce this in `ShopifyApp::WebhookVerification` and the generator templates so downstream app code doesn't inadvertently trust unverified fields.

### Proof of Concept
1. Capture a legitimate Shopify webhook request destined for `shop-a.myshopify.com` with body `B` and a valid `X-Shopify-Hmac-Sha256` header computed over `B`.
2. Replay/forward the identical request to the app's webhook endpoint, changing only the header `X-Shopify-Shop-Domain: shop-b.myshopify.com` (leave `B` and the HMAC header untouched).
3. `ShopifyApp::WebhookVerification#verify_request` → `hmac_valid?(request.raw_post)` still succeeds because it only checks `B` against the HMAC.
4. Any controller/job following the documented pattern (`SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`) now processes shop A's webhook body as if it belonged to shop B.

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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L1-12)
```text
# frozen_string_literal: true

module Webhooks
  class <%= @controller_class_name %> < ApplicationController
    include ShopifyApp::WebhookVerification

    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
    end
  end
```
