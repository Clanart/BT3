#### Title
Webhook `shop_domain` is derived from an unverified header, allowing cross-shop confusion via HMAC replay - (File: lib/shopify_app/controller_concerns/webhook_verification.rb)

#### Summary
`ShopifyApp::WebhookVerification#verify_request` only checks that `hmac_valid?(request.raw_post)` matches the raw body against the app's shared client secret; it never binds that HMAC to the value returned by the `shop_domain` private method, which simply reads the attacker-controllable `X-Shopify-Shop-Domain` header. Because Shopify's webhook HMAC covers only the body (not headers), a body/HMAC pair legitimately generated for one installed shop remains valid if replayed with the header changed to point at a different shop.

#### Finding Description
`verify_request` in `lib/shopify_app/controller_concerns/webhook_verification.rb` computes `hmac_valid?(data)` from `request.raw_post` and the app's `ShopifyApp.configuration.secret`/`old_secret` [1](#0-0) [2](#0-1) . Separately, `shop_domain` reads `request.headers["HTTP_X_SHOPIFY_SHOP_DOMAIN"]` directly, with no cryptographic tie to the HMAC or body [3](#0-2) . `WebhooksController#receive` then calls `params.permit!` and forwards `request.headers.to_h` (including the untrusted shop-domain header) straight into `ShopifyAPI::Webhooks::Registry.process` [4](#0-3) .

Since the app's client secret is shared across every shop that installs the app (it is not per-shop), and the Shopify HMAC signature is computed over the raw body only, a party that legitimately receives a webhook for their own installed shop (e.g. by installing the app on `attacker.myshopify.com`) can capture a valid `(body, HMAC)` pair and replay it against the same endpoint while substituting `X-Shopify-Shop-Domain: shop-victim.myshopify.com`. `hmac_valid?` still returns true because it never inspects the header, so `verify_request` passes and the request is dispatched with a shop-domain claim that was never authenticated.

#### Impact Explanation
Because `Registry.process` (from the `shopify_api` gem, outside this repo) and any handlers registered via `ShopifyApp::WebhooksManager` key their business logic (e.g., clearing session/token data on `app/uninstalled`, or updating stored access scopes on `app/scopes_update`) off the shop domain taken from the header, this creates a cross-shop confusion vector: an attacker-controlled shop's genuine webhook traffic can be replayed to make the app believe an unrelated victim shop sent that event. Depending on which webhook topic is replayed, this could trigger deletion/invalidation of the victim's stored session/token or corrupt its recorded access-scope state — matching the "cross-shop session/token confusion via forged webhook" impact class.

#### Likelihood Explanation
Exploitability requires the attacker to (a) install the app on a shop they control (a normal, unprivileged action) and receive a real webhook to capture a valid body/HMAC pair, and (b) be able to send an arbitrary HTTP request to the app's public webhook endpoint with a modified `X-Shopify-Shop-Domain` header — both fully within the "unprivileged attacker" threat model defined in the rules (merchant controlling an unrelated shop, crafting arbitrary headers). No app secret, victim session, or leaked token is needed. This is realistically repeatable for any shop-scoped webhook topic.

#### Recommendation
Never trust `X-Shopify-Shop-Domain` for identifying which shop record to mutate. Either (1) derive the shop identity strictly from the verified webhook payload itself (most Shopify webhook payloads embed the shop's `myshopify_domain`/`shop_id`) and cross-check it against the header, rejecting mismatches, or (2) look up the shop by an identifier already bound to a previously-established, verified relationship (e.g., only accept the header if a shop record with that domain and a session actually exists, and additionally verify with `ActiveSupport::SecurityUtils.secure_compare` that a shop-specific secret, if configured, matches) before acting on it in the webhook handler.

#### Proof of Concept
```ruby
test "hmac_valid? does not bind HMAC to X-Shopify-Shop-Domain header" do
  body = '{"myshopify_domain":"attacker.myshopify.com"}'
  hmac = Base64.strict_encode64(OpenSSL::HMAC.digest("sha256", ShopifyApp.configuration.secret, body))

  # Legit request as attacker's own shop
  post "/webhooks", params: body, headers: {
    "HTTP_X_SHOPIFY_HMAC_SHA256" => hmac,
    "HTTP_X_SHOPIFY_SHOP_DOMAIN" => "attacker.myshopify.com",
  }
  assert_response :ok

  # Replay identical body/HMAC but forge the shop domain header
  post "/webhooks", params: body, headers: {
    "HTTP_X_SHOPIFY_HMAC_SHA256" => hmac,
    "HTTP_X_SHOPIFY_SHOP_DOMAIN" => "shop-victim.myshopify.com",
  }
  # Expected (secure): :unauthorized, because the header is not covered by the signature
  # Actual (current code): :ok — verify_request only checks hmac_valid?(body)
  assert_response :ok
end
```
This demonstrates that `verify_request` accepts the forged header unconditionally as long as the body/HMAC pair is valid, confirming that shop binding is not derived from any verified claim.

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
