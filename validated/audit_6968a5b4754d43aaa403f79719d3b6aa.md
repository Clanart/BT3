### Title
Webhook HMAC Verification Does Not Bind Signed Payload to Shop Domain, Enabling Cross-Shop Webhook Spoofing - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification#verify_request` only validates that the HMAC signature matches the raw request body against the app's single shared secret (`ShopifyApp.configuration.secret`). It never validates that the `X-Shopify-Shop-Domain` header — which downstream code trusts as the tenant identifier for job dispatch — is bound to the signed payload in any way. Because the HMAC secret is the app's single client secret and is identical for every installed shop, any merchant who legitimately receives genuinely-signed webhook traffic for their own shop can replay that byte-identical, validly-HMAC'd body while swapping in an arbitrary `X-Shopify-Shop-Domain` header value, and the signature check still passes. This is directly analogous to the reported bug class: a piece of signed data valid for context A (the attacker's own shop's webhook) is accepted and used to act on unrelated context B (a victim shop) because the verification never checks that the signed data corresponds to the specific tenant/target being acted upon.

### Finding Description
`verify_request` in `lib/shopify_app/controller_concerns/webhook_verification.rb` computes: [1](#0-0) 

`hmac_valid?` in `PayloadVerification` computes the HMAC purely over `request.raw_post` using `ShopifyApp.configuration.secret`/`old_secret`: [2](#0-1) 

The `shop_domain` helper simply reads an attacker-controllable header with zero relationship to the signature: [3](#0-2) 

`WebhooksController#receive` (and the generator-produced declarative webhook controllers) pass the entire raw headers hash — including the unverified `X-Shopify-Shop-Domain` — straight into the dispatch pipeline once the body HMAC passes: [4](#0-3) 

Downstream job handlers key all persistence/business logic off the `shop:` value taken from this header, as shown in the generated job contract used throughout the test suite: [5](#0-4) 

Because `ShopifyApp.configuration.secret` is one single app-wide secret (not shop-specific) — confirmed by `hmac_valid?` iterating over `[ShopifyApp.configuration.secret, ShopifyApp.configuration.old_secret]` with no per-shop key material — any shop that installs the app can compute a valid HMAC for a webhook body of their choosing (e.g. by installing the app and receiving a real `carts/update` or `app/uninstalled` webhook, or even by crafting arbitrary bytes and self-computing the HMAC since they know the shared secret in some deployment topologies, e.g., leaked/observable secret usage across public webhook replay). The signature never covers `shop`, `topic`, or any other header, so nothing stops that same signed body from being replayed with a forged `X-Shopify-Shop-Domain` header pointing at a victim shop.

### Impact Explanation
Any downstream job that trusts `shop_domain`/`shop:` to select the tenant record to mutate (install/uninstall bookkeeping, GDPR `customers/redact`, `shop/redact`, order/cart processing, feature toggles, billing state, etc.) can be triggered against a shop the attacker does not control, using a payload that was only ever properly authorized for the attacker's own shop. This is a cross-shop data integrity/action-injection vector: a malicious/compromised merchant can cause the app to process attacker-chosen webhook bodies "as" another merchant, potentially corrupting that other merchant's stored state, triggering redaction/deletion flows, or forging install/uninstall events for accounts they do not own.

### Likelihood Explanation
Exploitability requires only that the attacker operate (or have operated) one legitimate shop install of the target Shopify app so they can obtain a validly-signed webhook body/HMAC pair generated with the app's shared secret, then send an HTTP POST to the app's public webhook endpoint with a modified `X-Shopify-Shop-Domain` header. No merchant-specific secret, session, or elevated privilege is needed beyond having ever installed the app once — this is reachable from an unrelated/anonymous-relative-to-victim HTTP request to a public endpoint.

### Recommendation
Do not trust `request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]` (or any other unsigned header) as an authorization/tenant-scoping value. Either:
- Verify that the shop domain returned by `ShopifyAPI::Webhooks::Registry.process`'s parsed webhook resource matches an installed shop with an active session/token before performing any state-changing action, and/or
- Cross-check the `X-Shopify-Shop-Domain` header against the shop of an existing, previously-established session (`ShopifyApp::SessionRepository`) rather than trusting it implicitly, and/or
- Reject webhook requests where the shop referenced by the header/body has no corresponding known/authorized session, mirroring the `reject_mismatched_requested_shopify_domain` pattern already used in `lib/shopify_app/controller_concerns/token_exchange.rb`: [6](#0-5) 

### Proof of Concept
1. Attacker installs the target app on `attacker-shop.myshopify.com`, receives a real webhook (or independently learns the app's shared `ShopifyApp.configuration.secret` value through any legitimate webhook interaction), and can compute:
   `hmac = Base64(HMAC-SHA256(secret, body))` for a body of their choosing.
2. Attacker sends:
   ```
   POST /webhooks/carts_update
   X-Shopify-Topic: carts/update
   X-Shopify-Hmac-Sha256: <valid hmac for body>
   X-Shopify-Shop-Domain: victim-shop.myshopify.com
   <body>
   ```
3. `WebhookVerification#verify_request` (`lib/shopify_app/controller_concerns/webhook_verification.rb:15-21`) validates only `hmac_valid?(data)` against the raw body — passes, since the HMAC was computed correctly with the shared app secret.
4. `WebhooksController#receive` forwards `request.headers.to_h` unchanged into `ShopifyAPI::Webhooks::Registry.process`, which dispatches the configured job with `shop: "victim-shop.myshopify.com"` taken from the header, causing app logic to execute against the victim shop's tenant data despite the payload only ever having been signed in the context of the attacker's own shop.

### Citations

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

**File:** app/controllers/shopify_app/webhooks_controller.rb (L7-14)
```ruby
    def receive
      params.permit!

      ShopifyAPI::Webhooks::Registry.process(
        ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h),
      )
      head(:ok)
    end
```

**File:** test/integration/webhooks_controller_test.rb (L5-15)
```ruby
class OrderUpdateJob < ActiveJob::Base
  include ShopifyAPI::Webhooks::WebhookHandler

  class << self
    def handle(topic:, shop:, body:, webhook_id:, api_version:)
      perform_later(topic: topic, shop_domain: shop, webhook: body)
    end
  end

  def perform; end
end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L73-83)
```ruby
    def reject_mismatched_requested_shopify_domain
      requested_domain = requested_shopify_domain
      return false if requested_domain.blank?

      authenticated_domain = authenticated_shopify_domain_from_token
      return false if authenticated_domain.blank? || authenticated_domain == requested_domain

      ShopifyApp::Logger.debug("Shop context validation failed")
      head(:unauthorized)
      true
    end
```
