### Title
Webhook `shop_domain` is taken from an unsigned HTTP header, allowing cross-shop confusion via HMAC-valid replayed webhooks - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification#shop_domain` returns `request.headers["HTTP_X_SHOPYFY_SHOP_DOMAIN"]`/`X_SHOPIFY_SHOP_DOMAIN` verbatim, while `hmac_valid?` (in `PayloadVerification`) only authenticates `request.raw_post`. Because the shop-domain header is never covered by the HMAC, an attacker who controls a shop that has installed the app can replay a validly-signed webhook body from their own store with an altered `X-Shopify-Shop-Domain` header pointing at a victim shop, and the request will still pass `verify_request`.

### Finding Description
`verify_request` in `lib/shopify_app/controller_concerns/webhook_verification.rb:15-21` only checks `hmac_valid?(request.raw_post)`; `hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb:13-23` computes `OpenSSL::HMAC.digest` over the raw body only, using the app's shared client secret. `shop_domain` (webhook_verification.rb:23-25) simply reads the shop-domain header with no cross-check against the signed payload. The documented generator pattern (`lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt:8-9`) forwards `webhook_request.shop` (also derived purely from headers by the `shopify_api` gem, not the HMAC) into `perform_later(shop_domain: ..., webhook: ...)`, and the companion job template (`lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt:8-9`) uses that unverified `shop_domain` to look up `Shop.find_by(shopify_domain: shop_domain)` and then runs webhook processing logic under that shop's session. Since the app's HMAC secret is shared across all shops that install the app, any shop owner (including an attacker's own dev/test store) can legitimately obtain a body+HMAC pair that satisfies `hmac_valid?`; nothing in the library binds that HMAC-verified body to the specific shop asserted by the header.

### Impact Explanation
An attacker who has installed the app on their own store can replay one of their own store's real webhooks with a forged `X-Shopify-Shop-Domain` header set to a victim shop. Because `hmac_valid?` and `shop_domain` are independent checks, the forged request passes verification and the job template resolves and processes it against the victim's `Shop` record, causing cross-shop data confusion (job logic executes attacker-controlled webhook payload content attributed to the victim shop's session/token). This aligns with the Shopify HackerOne "cross-shop data leakage/confusion" impact class, scoped to apps that follow the documented generator pattern.

### Likelihood Explanation
Exploitability requires only that the attacker operate a shop with the target app installed (any merchant can install a public app) and capture one legitimately-signed webhook body/HMAC pair from their own store — no secrets, tokens, or victim interaction are needed. The attack is trivially repeatable for every webhook topic implemented via the documented generator pattern.

### Recommendation
Do not trust `X-Shopify-Shop-Domain`/`webhook_request.shop` as an authorization signal on its own. Either (a) verify that the shop asserted by the header matches a shop that is expected to receive that specific webhook topic/id via an independent, authenticated lookup, or (b) include the shop domain in the data that is cryptographically bound to the request before trusting it, and document that generated job templates must re-validate the shop against session/install state before performing shop-scoped writes.

### Proof of Concept
```ruby
test "forged shop-domain header is accepted with a validly-signed body from a different shop" do
  body = '{"id":1}'
  hmac = Base64.strict_encode64(
    OpenSSL::HMAC.digest(OpenSSL::Digest.new("sha256"), ShopifyApp.configuration.secret, body)
  )

  post webhooks_path("orders_update"),
       params: body,
       headers: {
         "CONTENT_TYPE" => "application/json",
         "HTTP_X_SHOPIFY_HMAC_SHA256" => hmac,
         "HTTP_X_SHOPIFY_SHOP_DOMAIN" => "victim-shop.myshopify.com", # forged, unrelated to signer
       }

  assert_response :ok # verify_request passes despite shop header being unverified
end
```
Expected (secure) behavior: the request should be rejected or the shop_domain should be independently validated before any job is dispatched; current behavior accepts it solely based on `hmac_valid?(request.raw_post)`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** lib/generators/shopify_app/add_webhook/templates/webhook_job.rb.tt (L8-17)
```text
  def perform(topic:, shop_domain:, webhook:)
    shop = Shop.find_by(shopify_domain: shop_domain)

    if shop.nil?
      logger.error("#{self.class} failed: cannot find shop with domain '#{shop_domain}'")

      raise ActiveRecord::RecordNotFound, "Shop Not Found"
    end

    shop.with_shopify_session do |session|
```
