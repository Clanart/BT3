### Title
Webhook shop-domain spoofing via unauthenticated `X-Shopify-Shop-Domain` header allows cross-shop webhook processing - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification` authenticates a webhook request only by HMAC-signing the raw POST body, but the shop identity used to dispatch/attribute the webhook — `shop_domain` — is read straight from the `X-Shopify-Shop-Domain` HTTP header, which is **not** included in the HMAC computation. Any request bearing a *valid* HMAC for a given body can carry an arbitrary shop-domain header, letting a request captured/produced for one (attacker-controlled) shop be replayed and re-attributed to a different, victim shop.

### Finding Description
`hmac_valid?` computes the digest over `request.raw_post` only: [1](#0-0) 

`verify_request` calls this and only rejects the request if the body HMAC is invalid — it never checks the shop header: [2](#0-1) 

The `shop_domain` helper — which the gem's own documentation instructs developers to use as the trusted shop identity when dispatching webhook jobs — simply returns the raw, unauthenticated header value: [3](#0-2) 

The documented usage pattern feeds this unauthenticated value directly into job dispatch, and the generator templates shipped with the gem do the same, resolving a `Shop` record purely from the header-derived `shop_domain`: [4](#0-3) [5](#0-4) 

This is the analog of the Curves `withdraw(amount = 0)` bug class: a security-relevant identifier (there: `amount`; here: the shop identity) is *not* validated/bound to the trusted computation (there: the balance/mint path; here: the HMAC-covered body) before being used to drive privileged, shop-scoped state (job execution, `Shop.find_by(shopify_domain: ...)` lookups, `shop.with_shopify_session`). Because Shopify's HMAC only covers the body, not headers, any actor who can obtain one valid `(body, HMAC)` pair — e.g., by legitimately installing the app on their own shop and capturing a real webhook — can resend that exact body/HMAC pair while substituting the `X-Shopify-Shop-Domain` header for a different, victim shop domain that also has the app installed.

### Impact Explanation
An attacker who installs the app on a shop they control (a normal, unprivileged, unrelated-merchant action) can capture a legitimately-signed webhook and replay it against the app's webhook endpoint with the header changed to point to a victim's `shopify_domain`. Since the sample/generated job pattern resolves `shop = Shop.find_by(shopify_domain: shop_domain)` and then calls `shop.with_shopify_session`, this results in the victim shop's offline session/access token being used to execute the attacker-supplied webhook body as if it originated from the victim — a cross-shop confused-deputy condition. Depending on the specific job logic, this can range from processing forged/replayed data under the wrong shop to triggering privileged actions (e.g., mandatory privacy jobs like `shop/redact`, `customers/redact`) against a shop the attacker doesn't own.

### Likelihood Explanation
Exploitation requires the attacker to obtain at least one valid `(raw_body, HMAC)` pair for the target topic, which is trivially achievable by installing the app on their own shop (a normal, unprivileged flow — no special access needed) and capturing the webhook their own shop legitimately receives. The victim shop only needs to also have the app installed. No secret disclosure or authentication bypass on the app's own credentials is required — the weakness is structural (headers not being covered by HMAC) and is directly exposed by the gem's documented API and generator templates.

### Recommendation
Do not treat `X-Shopify-Shop-Domain` as trusted shop context on its own. Either:
- Extract and validate the shop domain from the HMAC-covered request body (e.g. via `ShopifyAPI::Webhooks::Request`, which the built-in `WebhooksController#receive` already uses instead of the raw header), or
- Bind the header value into the HMAC computation before trusting it, or
- Cross-check the header-derived shop against an independent, previously-established relationship (installed session for that shop) tied to the specific webhook `id`/nonce to prevent replay across shops.
Update `docs/shopify_app/webhooks.md` and the `add_webhook`/`add_declarative_webhook` generator templates accordingly, since they currently propagate the unauthenticated header value directly into job dispatch.

### Proof of Concept
1. Install the target app on an attacker-owned shop `attacker.myshopify.com` and subscribe to a webhook topic (e.g. `orders/create`).
2. Capture a legitimate webhook delivery: body `B` and header `X-Shopify-Hmac-Sha256: H` (valid HMAC of `B` using the app secret) plus `X-Shopify-Shop-Domain: attacker.myshopify.com`.
3. Resend the exact same POST body `B` and HMAC header `H` to the app's webhook endpoint, but replace `X-Shopify-Shop-Domain` with `victim.myshopify.com` (a shop that also has the app installed).
4. `hmac_valid?` passes because `B`/`H` are a valid pair — the header is never checked: [6](#0-5) 
5. The job dispatched by the custom controller (per the documented pattern) or a generator-produced job resolves `Shop.find_by(shopify_domain: "victim.myshopify.com")` and executes the attacker's webhook body under the victim shop's session, per the templates shown above.

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

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L1-21)
```text
class <%= @job_class_name %> < ActiveJob::Base
  extend ShopifyAPI::Webhooks::WebhookHandler

  def self.handle(topic:, shop:, body:, webhook_id:, api_version:)
    perform_later(topic: topic, shop_domain: shop, webhook: body)
  end

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
end
```
