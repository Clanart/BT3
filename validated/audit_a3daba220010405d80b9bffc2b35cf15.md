### Title
Unverified `X-Shopify-Shop-Domain` header used as shop identity in `WebhookVerification#shop_domain`, enabling cross-shop job confusion via HMAC-valid webhook replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#shop_domain` returns the raw, unauthenticated `HTTP_X_SHOPIFY_SHOP_DOMAIN` header, while `verify_request`/`hmac_valid?` only verifies the HMAC over `request.raw_post` (the body) — the shop-domain header is never covered by the signature. The module's own documentation and generated code instruct developers to enqueue background jobs using `shop_domain: shop_domain`, meaning the "shop" the job runs against is bound only to an attacker-controllable header, not to any cryptographically signed field.

### Finding Description
The verification flow is:
- `verify_request` in `lib/shopify_app/controller_concerns/webhook_verification.rb` (lines 15-21) calls `hmac_valid?(request.raw_post)`.
- `hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb` (lines 13-23) computes `OpenSSL::HMAC.digest(digest, secret, data)` over `data` (i.e., `request.raw_post` only) and compares it to `HTTP_X_SHOPIFY_HMAC_SHA256`.
- `shop_domain` (lines 23-25 of `webhook_verification.rb`) simply reads `request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]` — a plain HTTP header that is **not** part of the HMAC-signed payload and is fully attacker-controlled on a raw HTTP request.

The gem explicitly documents and generates code that trusts this value as the tenant identity for background job dispatch:
- `docs/shopify_app/webhooks.md` (custom controller example, lines 88-104) shows `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)` using the module's `shop_domain` helper.
- The generated declarative webhook controller template `lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt` (lines 7-11) does the same via `webhook_request.shop` (also header-derived, from `ShopifyAPI::Webhooks::Request`), calling `<Job>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)`.

Because the signature only binds the body bytes, an attacker who has legitimately obtained one valid `(raw_body, HMAC)` pair for their own shop (e.g., by owning a test/dev store and receiving a real webhook, or capturing/replaying one) can resend that exact body and HMAC to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header value. `hmac_valid?` still returns `true` because the body/HMAC pair is unchanged and valid, so `verify_request` allows the request through. The controller then dispatches a background job scoped to the attacker-chosen `shop_domain` header, using data (webhook body) that legitimately belongs to the attacker's own shop but is now falsely attributed to the victim shop.

No existing check mitigates this: there is no comparison between the signed body's shop context and the header, no `ActiveSupport::SecurityUtils.secure_compare` on the domain, and no `ShopifyApp::Utils.sanitize_shop_domain` call in this path that would bind the header to a verified session or reject spoofing.

### Impact Explanation
This allows cross-tenant confusion: a victim shop's job queue/worker can be made to process attacker-supplied webhook payload data attributed to the victim's `shop_domain`. Depending on what the shop-specific job does (e.g., storing webhook data keyed by `shop_domain`, updating shop-level settings, deleting/redacting data, syncing shop-scoped records), this could corrupt or pollute the victim shop's stored data with attacker-controlled content, or cause the app to perform shop-scoped side effects (e.g., privacy/redaction jobs) against the wrong tenant. This matches Shopify's "Cross-tenant data access/confusion" impact class — background jobs are dispatched and act under an unverified, attacker-chosen shop identity even though the HMAC check passed.

The severity is bounded by two factors: (1) the attacker needs at least one genuinely HMAC-valid `(body, hmac)` pair, which in practice they can obtain by owning any shop that installs the app (a normal, unprivileged action) and receiving a real webhook from Shopify for their own store, and (2) the actual damage is limited to whatever the shop-scoped job does with the body content (job logic in the host app, not shown/verifiable here, determines exact blast radius).

### Likelihood Explanation
Feasible and repeatable: obtaining a valid signed webhook body only requires installing the app on any shop the attacker controls (a normal, permitted action for any merchant) and observing a legitimate webhook delivery to their own endpoint/logs, or being sent one via the app's own webhook system. Replaying that identical body+HMAC with a swapped `X-Shopify-Shop-Domain` header is trivial to script and can be repeated for any target shop domain, since nothing in `verify_request` or `hmac_valid?` binds the signature to a specific shop domain or to Shopify's origin IP.

### Recommendation
Do not trust `HTTP_X_SHOPIFY_SHOP_DOMAIN` as tenant identity unless it is cryptographically bound to the request. Options:
- Include the shop domain in the HMAC computation (Shopify's HMAC is computed over the raw body only by design, so this can't be changed unilaterally) — instead, extract the shop domain from a verified field inside the parsed/verified webhook body (many Shopify webhook payloads include shop-identifying data, or use the `X-Shopify-Shop-Id`/domain that Shopify itself derives server-side per topic) rather than an arbitrary header.
- At minimum, cross-check the header-derived `shop_domain` against a known/installed shop record (e.g., only accept it if it matches a shop already present in the app's shop/session store with a valid access token), rejecting unknown or mismatched domains before enqueuing shop-scoped jobs.
- Document prominently (beyond the current example) that `shop_domain` from `WebhookVerification` is **not** cryptographically verified and must not be trusted as sole tenant-scoping input without additional shop-existence/ownership validation.

### Proof of Concept
```ruby
# test/controllers/concerns/webhook_verification_cross_shop_test.rb
class WebhookVerificationController < ActionController::Base
  include ShopifyApp::WebhookVerification

  def webhook_action
    # mirrors documented usage pattern from docs/shopify_app/webhooks.md
    SomeJob.perform_later(shop_domain: shop_domain, webhook: params.except(:controller, :action).to_h)
    head :ok
  end
end

test "HMAC-valid body from attacker's shop is accepted under a spoofed victim shop_domain header" do
  ShopifyApp.configure { |c| c.secret = "secret" }

  body = { foo: "attacker-controlled-data" }.to_json
  valid_hmac = Base64.strict_encode64(
    OpenSSL::HMAC.digest("sha256", "secret", body)
  )

  @request.headers["HTTP_X_SHOPIFY_HMAC_SHA256"] = valid_hmac
  @request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"] = "victim-shop.myshopify.com" # attacker-chosen, not signed
  @request.env["RAW_POST_DATA"] = body

  post :webhook_action

  assert_response :ok # HMAC check passes: body/HMAC pair is valid
  assert_enqueued_with(job: SomeJob, args: [{ shop_domain: "victim-shop.myshopify.com", webhook: { "foo" => "attacker-controlled-data" } }])
  # Demonstrates job is enqueued for the *header's* shop, never cryptographically bound to it.
end
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L7-11)
```text
    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
    end
```
