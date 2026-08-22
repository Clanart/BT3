### Title
Webhook shop-domain header is not bound to the HMAC signature, allowing cross-shop misattribution of otherwise-valid signed webhooks - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification` validates that a webhook's *body* was signed with the app's shared secret, but the shop that the webhook is attributed to (`X-Shopify-Shop-Domain`) is read from a header that is **not** covered by the HMAC. Consequently, a validly-signed webhook body can be replayed with a different `X-Shopify-Shop-Domain` header and will still pass verification, causing downstream jobs to act on the wrong shop's record — the exact "authentic data, wrong owner attribution" pattern described in the source report (positions verified as real but attributed to the wrong vault).

### Finding Description
`hmac_valid?` computes/verifies the digest strictly over `request.raw_post`: [1](#0-0) 

`shop_domain` is derived independently from a request header that is never included in that signed payload: [2](#0-1) 

Because the shop attribution is completely decoupled from the cryptographic proof of authenticity, any attacker who possesses one valid `(raw_body, HMAC)` pair for the app (e.g. from a webhook legitimately delivered by Shopify for a shop they control/install the app on) can resend that identical body/HMAC to the app's webhook endpoint while substituting an arbitrary victim `X-Shopify-Shop-Domain` header. `verify_request` will still accept it since it only checks the body's HMAC: [3](#0-2) 

The documented pattern for consuming this data forwards the unverified header value directly into background jobs that look up and mutate the victim's `Shop` record: [4](#0-3) 

The generated privacy/uninstall job templates then perform destructive operations keyed solely on that unverified `shop_domain`: [5](#0-4) [6](#0-5) 

### Impact Explanation
An attacker can force the app to process an "authenticated" webhook against a **different, unrelated merchant's shop record** instead of their own. Depending on which webhook job is targeted, this ranges from misattributed data-processing to destructive actions on the victim's stored session (e.g. `AppUninstalledJob` calling `shop.destroy`, deleting the victim's persisted access token/session record) or firing `shop/redact` / `customers/redact` logic against a shop the attacker does not control. This is a cross-shop state-changing impact triggered purely by a forged/replayed but signature-valid HTTP request.

### Likelihood Explanation
Likelihood is medium: exploitation requires the attacker to obtain at least one legitimately-signed `(body, HMAC)` pair, which is trivial for anyone who installs the app on their own store (Shopify sends them real signed webhooks, e.g. `app/uninstalled`, on ordinary lifecycle events they fully control). No secrets need to be leaked — only the header needs to be altered on replay, since the header was never part of what's being verified.

### Recommendation
Bind the shop identity to the signed payload verification instead of trusting the `X-Shopify-Shop-Domain` header independently:
- Where feasible, extract and trust the shop only from a value embedded in the verified body (or from Shopify's official webhook SDK helper that ties shop + HMAC verification together atomically), not from a separate unauthenticated header.
- At minimum, cross-check that the shop implied by the signed payload matches the shop referenced in the header before dispatching any job, and reject the request if not.
- Update `docs/shopify_app/webhooks.md`'s example and the job generator templates (`add_app_uninstalled_job`, `add_privacy_jobs`, `add_webhook`, `add_declarative_webhook`) so they don't treat the header-derived `shop_domain` as an authenticated value for record lookup/destruction.

### Proof of Concept
1. Attacker installs the target app on their own development shop `attacker.myshopify.com`.
2. Attacker uninstalls the app (or triggers any subscribed webhook topic), causing Shopify to POST a webhook to the app's `WebhooksController#receive` endpoint with a body `B` and a valid `X-Shopify-Hmac-Sha256` header `H` computed from the shared app secret, plus `X-Shopify-Shop-Domain: attacker.myshopify.com`.
3. Attacker captures/replays this exact request but changes only the `X-Shopify-Shop-Domain` header to `victim.myshopify.com`, keeping body `B` and HMAC `H` identical.
4. `WebhookVerification#verify_request` ( [3](#0-2) ) recomputes the HMAC over `B` only, finds it valid, and lets the request proceed.
5. The resulting job (e.g. `AppUninstalledJob`) looks up `Shop.find_by(shopify_domain: "victim.myshopify.com")` and destroys/redacts that record — a cross-shop state change caused by a forged but "verification-passing" webhook ( [5](#0-4) ).

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

**File:** lib/generators/shopify_app/add_app_uninstalled_job/templates/app_uninstalled_job.rb.tt (L8-19)
```text
  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")
      
      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    logger.info("#{self.class} started for shop '#{shop_domain}'")
    shop.destroy
  end
```

**File:** lib/generators/shopify_app/add_privacy_jobs/templates/shop_redact_job.rb.tt (L8-19)
```text
  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")
      
      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    shop.with_shopify_session do
    end
  end
```
