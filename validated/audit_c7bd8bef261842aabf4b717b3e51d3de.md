### Title
`ShopifyApp::WebhookVerification#shop_domain` returns unsanitized, unauthenticated `X-Shopify-Shop-Domain` header, allowing cross-shop webhook attribution/replay - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Finding Description
`ShopifyApp::WebhookVerification#verify_request` computes HMAC validity using `hmac_valid?(data)` where `data = request.raw_post` [1](#0-0) . The actual HMAC comparison in `PayloadVerification#hmac_valid?` is computed solely over the raw request body against `ShopifyApp.configuration.secret` (or `old_secret`) — it never includes or binds any request headers, including `HTTP_X_SHOPIFY_SHOP_DOMAIN` [2](#0-1) . Separately, `shop_domain` simply returns the raw header value with no call to `ShopifyApp::Utils.sanitize_shop_domain` or any other validation [3](#0-2) .

Because the app's client secret is shared across every shop that installs the app (it is not shop-specific), any merchant who has installed the app can legitimately trigger an event in their own store and receive a genuinely-signed webhook (valid body + valid HMAC) from Shopify to the app's public webhook endpoint. That merchant fully controls the request once it reaches their network path to replay: since the header is excluded from the HMAC signature, they can resend the exact same `body`+`HMAC` pair while substituting `X-Shopify-Shop-Domain` with an arbitrary victim shop's domain (or an injection string). `verify_request` still passes because the body/HMAC pair is untouched, and `shop_domain` will return the attacker's chosen header value unchecked.

The documented usage pattern for custom controllers built on this concern feeds that unauthenticated value directly into job attribution: `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)` [4](#0-3) , and the generator-produced jobs use it to look up the shop record and act with that shop's session/token: `shop = Shop.find_by(shopify_domain: shop_domain)` followed by `shop.with_shopify_session` [5](#0-4) . No sanitization or cross-check ties the header-derived `shop_domain` back to the shop that actually produced the signed body.

### Impact Explanation
An attacker who legitimately installed the app on their own shop (or otherwise obtained one valid signed webhook body/HMAC pair) can replay it with a forged `X-Shopify-Shop-Domain` header to make the app process/attribute attacker-controlled webhook content as belonging to an arbitrary victim shop. Downstream, this results in cross-shop confusion: jobs keyed by `shop_domain` will run business logic (using the victim shop's stored access token via `with_shopify_session`) against attacker-supplied payload data, effectively a forged/spoofed webhook event delivered under another shop's identity. This maps to Shopify's "forged webhook/signed request accepted" impact class.

### Likelihood Explanation
Exploitation requires: (1) the app to use the documented `ShopifyApp::WebhookVerification` pattern in a custom controller/job that trusts `shop_domain` for attribution (as shown in the official docs and generator templates), and (2) the attacker to obtain at least one valid signed body/HMAC pair — achievable by installing the app on their own shop and triggering a real webhook, since the shared secret is not shop-specific. This is realistic and repeatable (the attacker can generate arbitrarily many valid body/HMAC pairs from their own shop's activity), not a one-off race condition.

### Recommendation
Do not use the raw `X-Shopify-Shop-Domain` header as an authenticated identity signal. At minimum, run it through `ShopifyApp::Utils.sanitize_shop_domain` before use, and — more importantly — cross-verify that the shop the header claims matches the shop actually entitled to receive that webhook body (e.g., verify against `ShopifyAPI::Webhooks::Request` parsing that ties shop/topic/webhook-id together, or independently validate ownership before enqueuing shop-attributed jobs). Consider documenting/enforcing that `shop_domain` must be sanitized in all downstream usages.

### Proof of Concept
```ruby
class CustomWebhooksController < ActionController::Base
  include ShopifyApp::WebhookVerification

  def carts_update
    params.permit!
    RecordedJob.perform_later(shop_domain: shop_domain, webhook: params.except(:controller, :action).to_h)
    head :no_content
  end
end

# test
test "accepts forged shop_domain header alongside a validly-HMAC'd body" do
  body = '{"id":1}'
  secret = ShopifyApp.configuration.secret
  hmac = Base64.strict_encode64(OpenSSL::HMAC.digest("sha256", secret, body))

  post "/webhooks/carts_update",
    params: body,
    headers: {
      "HTTP_X_SHOPIFY_HMAC_SHA256" => hmac,
      "HTTP_X_SHOPIFY_SHOP_DOMAIN" => "victim-shop.myshopify.com", # attacker-chosen, unrelated to who actually signed/sent this body
      "CONTENT_TYPE" => "application/json",
    }

  assert_response :no_content
  # RecordedJob was enqueued with shop_domain: "victim-shop.myshopify.com"
  # even though sanitize_shop_domain was never invoked and the header
  # is not covered by the HMAC signature.
end
```
Expected/actual: the request passes `verify_request` (HMAC only checks body), and `shop_domain` returns the raw, unsanitized, attacker-chosen header value, which is what gets enqueued to the job. [6](#0-5)

### Citations

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

**File:** docs/shopify_app/webhooks.md (L89-96)
```markdown
class CustomWebhooksController < ApplicationController
  include ShopifyApp::WebhookVerification

  def carts_update
    params.permit!
    SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)
    head :no_content
  end
```

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L8-19)
```text
  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")

      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    shop.with_shopify_session do |session|
    ## webhook processing logic
    end
```
