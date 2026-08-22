### Title
Webhook shop/topic attribution relies solely on unauthenticated headers, not on HMAC-covered data - ([File: lib/shopify_app/controller_concerns/payload_verification.rb], [File: lib/shopify_app/controller_concerns/webhook_verification.rb], [File: app/controllers/shopify_app/webhooks_controller.rb])

### Summary
`PayloadVerification#hmac_valid?` (and the `WebhookVerification#verify_request` before_action that calls it) only authenticates `request.raw_post` — the raw body bytes — against the shared app secret. The `X-Shopify-Topic` and `X-Shopify-Shop-Domain` headers are never included in the HMAC computation, yet `WebhooksController#receive` forwards `request.headers.to_h` unmodified into `ShopifyAPI::Webhooks::Registry.process`, which derives job routing and shop attribution from those same unauthenticated headers.

### Finding Description
`hmac_valid?` computes `OpenSSL::HMAC.digest(digest, secret, data)` over `data` only (the raw body) and compares it to the `HTTP_X_SHOPIFY_HMAC_SHA256` header via `secure_compare`: [1](#0-0) 

`WebhookVerification#verify_request` calls this with only `request.raw_post` as the signed data, and rejects the request only if that body-only HMAC fails: [2](#0-1) 

Crucially, `ShopifyApp.configuration.secret` (and `old_secret`) is a single, app-wide value set once in the initializer — not per-shop: [3](#0-2) 

`WebhooksController#receive` then passes the full, attacker-observable header set — including `X-Shopify-Topic` and `X-Shopify-Shop-Domain` — straight into `ShopifyAPI::Webhooks::Registry.process`, which uses those headers (not anything HMAC-bound) to select the job handler and to determine which shop the payload is attributed to: [4](#0-3) 

Because the signing secret is shared across every shop that has the app installed, any merchant who legitimately receives a genuinely-signed webhook for their own shop (e.g. by editing their own store data to trigger a real webhook, whose body content they can influence) possesses a valid `(body, hmac)` pair. Nothing in `verify_request` or `receive` binds that pair to the topic or shop-domain headers that were originally delivered alongside it. That merchant can replay the same body+HMAC with a forged `X-Shopify-Topic` and/or `X-Shopify-Shop-Domain` header, and the request still passes `hmac_valid?` because the check only covers the byte-identical body. Downstream, `Registry.process` and the app's own webhook job (e.g. `perform_later(shop_domain: ..., webhook: body)` as documented) will process/attribute the request under the attacker-chosen topic/shop rather than the one the body was actually issued for: [5](#0-4) 

No before_action, sanitizer, or comparison anywhere in `WebhookVerification` or `WebhooksController` checks that the `shop_domain`/topic headers match an expected value tied to the HMAC-signed payload; `shop_domain` is read directly from the header with no cross-check: [6](#0-5) 

### Impact Explanation
This enables cross-shop data/job misattribution: a merchant-attacker can cause the app to enqueue/process a webhook job under a victim shop's domain and/or under a topic different from the one the payload was actually signed for, using their own genuinely-signed traffic. Depending on the app's job implementation (which typically trusts `shop_domain` from the webhook to select which shop's records to update — as shown in the generator template and docs), this can lead to writing/mutating data attributed to a shop the attacker doesn't own, or invoking a handler with a body schema it didn't expect (topic confusion). This corresponds to a "Broken Authentication / Improper Verification of Cryptographic Signature scope" class of finding — the signature does not cover all data the code trusts as authenticated.

### Likelihood Explanation
Requires only: (1) the attacker's own shop has the app installed and can trigger at least one real webhook delivery (trivial, e.g. update a product), and (2) the app's secret is a single shared value across shops, which is the standard/only configuration this gem supports. No victim credentials, tokens, or host misconfiguration are needed — this is fully reachable by an unprivileged merchant attacker as defined in scope.

### Recommendation
Bind the topic and shop-domain to the HMAC-verified payload rather than trusting them as free-standing headers: either include them in the signed data used by `hmac_valid?`, or cross-validate the shop-domain header against a shop known to the app (e.g. an existing `ShopifyApp::SessionRepository` record) and validate the topic against the body content/registered subscription for that shop before dispatching to `Registry.process`.

### Proof of Concept
```ruby
# 1. Attacker's own shop legitimately receives a real webhook body B with valid HMAC H
#    (secret is shared across all shop installations of the app).
# 2. Attacker replays (B, H) to POST /webhooks/:type with:
post shopify_app.webhooks_path("orders_create"),
  params: nil,
  body: B, # raw bytes identical to the originally captured body
  headers: {
    "x-shopify-hmac-sha256" => H,                 # unchanged, still validates against B
    "x-shopify-topic" => "customers/data_request", # attacker-chosen, forged
    "x-shopify-shop-domain" => "victim-shop.myshopify.com", # attacker-chosen, forged
  }
# Expected (vulnerable) result: request passes verify_request (200/head :ok),
# and Registry.process dispatches body B under topic "customers/data_request"
# and shop "victim-shop.myshopify.com" even though neither was part of the HMAC.
#
# Expected (fixed) result: request should be rejected, or shop/topic attribution
# should be provably derived only from HMAC-covered data.
```

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

**File:** lib/shopify_app/configuration.rb (L8-11)
```ruby
    attr_accessor :application_name
    attr_accessor :api_key
    attr_accessor :secret
    attr_accessor :old_secret
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

**File:** docs/shopify_app/webhooks.md (L36-38)
```markdown
When the [OAuth callback](/docs/shopify_app/authentication.md#oauth-callback) or token exchange is completed successfully, ShopifyApp will queue a background job which will ensure all the specified webhooks exist for that shop. Because this runs on every OAuth callback, it means your app will always have the webhooks it needs even if the user uninstalls and re-installs the app.

ShopifyApp also provides a [WebhooksController](/app/controllers/shopify_app/webhooks_controller.rb) that receives webhooks and queues a job based on the received topic. For example, if you register the webhook from above, then all you need to do is create a job called `CartsUpdateJob`. The job will be queued with 2 params: `shop_domain` and `webhook` (which is the webhook body).
```
