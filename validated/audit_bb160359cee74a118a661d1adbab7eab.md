### Title
Webhook shop identity spoofing via unbound `X-Shopify-Shop-Domain` header — ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
The HMAC check performed before `ShopifyApp::WebhooksController#receive` (and any custom controller including `ShopifyApp::WebhookVerification`) validates only the raw request body against the app secret; it never binds the `X-Shopify-Shop-Domain` header into that signature. Consequently, an attacker who obtains one legitimately-signed webhook body (e.g., by triggering a webhook on a shop they control) can replay that exact body+HMAC while swapping the shop-domain header to point at a different (victim) shop, and the verification will still pass.

### Finding Description
`receive` is guarded by a `before_action :verify_request` from `ShopifyApp::WebhookVerification`: [1](#0-0) 

`verify_request` calls `hmac_valid?`, which is defined in `PayloadVerification` and computes/verifies an HMAC-SHA256 solely over `request.raw_post`: [2](#0-1) 

The shop identity, however, is derived from a separate, unsigned header: [3](#0-2) 

The project's own documented pattern for custom webhook controllers explicitly uses this unbound `shop_domain` value as trusted identity to enqueue background jobs that touch shop data: [4](#0-3) 

And the generator-produced default job template does a `Shop.find_by(shopify_domain: shop_domain)` lookup using that same value: [5](#0-4) 

Because `hmac_valid?` only covers `data = request.raw_post` [6](#0-5) , the header carrying shop identity is not cryptographically bound to the signed payload. An attacker who legitimately receives one signed webhook for a shop they control (a permitted "unprivileged attacker controlling an unrelated shop" per the threat model) can capture that raw body + its valid `X-Shopify-Hmac-Sha256` value, then replay the identical body/HMAC pair to the same endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header (e.g., a victim's `.myshopify.com` domain). `hmac_valid?` still returns true because the body is unchanged, so `verify_request` passes and the request reaches `receive`, which forwards the (attacker-controlled) headers wholesale into `ShopifyAPI::Webhooks::Request.new(... headers: request.headers.to_h)`. Downstream processing (Registry dispatch, or any custom controller built per the documented pattern) then acts on the attacker-forged shop identity with attacker-supplied payload content, which is exactly the "shop-domain header trusted as identity without HMAC binding" condition described in the invariant.

Note: The core dispatch logic inside `ShopifyAPI::Webhooks::Registry.process` and `ShopifyAPI::Webhooks::Request#shop` live in the external `shopify_api` gem, not in this repository, so I cannot fully confirm from this codebase alone whether that gem internally re-derives/validates the shop domain independently of the header. What is confirmed in-repo is that (a) this app's own `WebhookVerification` concern exposes and the documentation explicitly recommends using the raw, HMAC-unbound `shop_domain` header as trusted shop identity, and (b) the app's HMAC check never incorporates that header.

### Impact Explanation
If exploited, this allows cross-shop data spoofing: an attacker replaying a body they legitimately obtained (signed for their own shop) with a forged `X-Shopify-Shop-Domain` header can cause a victim shop's background job/data pipeline to process attacker-chosen webhook content, matching the "forged webhook request" / cross-shop confusion impact class relevant to Shopify's HackerOne program.

### Likelihood Explanation
Exploitability is constrained: the attacker must first obtain at least one validly-signed body+HMAC pair (feasible for an "attacker controlling an unrelated shop" by triggering a real webhook to their own installation), then simply resend it with a different shop-domain header — no app secret is required for this step, only observation of one legitimate delivery. This is straightforward and repeatable once a single valid sample is captured, and no timestamp/nonce/replay protection exists in this code path to prevent resubmission.

### Recommendation
Do not treat `X-Shopify-Shop-Domain` (or any header) as authenticated identity unless it is cryptographically bound to the signed payload. At minimum: (1) derive shop identity strictly from the verified webhook body's own shop-identifying fields (or from the `shopify_api` gem's authenticated `Request#shop` if it independently validates this), never from a raw header alone; (2) add replay protection (e.g., dedupe by `X-Shopify-Webhook-Id` with a TTL store) so identical valid artifacts cannot be reprocessed; (3) update `docs/shopify_app/webhooks.md`'s custom-controller example so it does not encourage trusting `shop_domain` as an identity source without further verification.

### Proof of Concept
```ruby
# test/shopify_app/controller_concerns/webhook_verification_test.rb (new case)
test "hmac check does not bind shop-domain header, allowing replay with forged shop" do
  with_application_test_routes do
    params = { foo: "anything" }
    valid_hmac = "yCGX/RrK4fcuNtr3ztk5tQGsOBjcAzHpGLdMUrbV8yI=" # valid HMAC for body {"foo":"anything"} w/ new secret

    # Original legitimate delivery, e.g. for attacker's own shop:
    @request.headers["HTTP_X_SHOPIFY_HMAC_SHA256"] = valid_hmac
    @request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"] = "attacker-shop.myshopify.com"
    post :webhook_action, params: params
    assert_response :ok

    # Replay of the *same* body/HMAC but forged shop-domain header (victim shop):
    @request.headers["HTTP_X_SHOPIFY_HMAC_SHA256"] = valid_hmac
    @request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"] = "victim-shop.myshopify.com"
    post :webhook_action, params: params
    assert_response :ok  # <-- still accepted; shop identity was never bound to the HMAC
  end
end
```
This demonstrates that `verify_request`/`hmac_valid?` accepts the replayed body regardless of the shop-domain header value, confirming the header is not bound by the HMAC and can be forged to spoof shop identity downstream. [7](#0-6)

### Citations

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L8-21)
```ruby
    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_request
    end

    private

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

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L8-15)
```text
  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")

      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end
```

**File:** test/shopify_app/controller_concerns/webhook_verification_test.rb (L38-46)
```ruby
  test "authorized requests should be successful" do
    with_application_test_routes do
      params = { foo: "anything" }
      valid_hmac = "yCGX/RrK4fcuNtr3ztk5tQGsOBjcAzHpGLdMUrbV8yI=" # Valid hmac using the new secret
      @request.headers["HTTP_X_SHOPIFY_HMAC_SHA256"] = valid_hmac
      post :webhook_action, params: params
      assert_response :ok
    end
  end
```
