### Title
Webhook shop-domain header is not covered by HMAC verification, allowing cross-shop event forgery via `WebhookVerification` - (File: lib/shopify_app/controller_concerns/webhook_verification.rb)

### Summary
`ShopifyApp::WebhookVerification#verify_request` validates only the request body's HMAC signature, while `#shop_domain` — the value app developers are told to use to scope/attribute the webhook to a specific store — is read directly from an unauthenticated header. Because the header is not part of the signed payload, any actor who can produce (or capture) a validly-signed webhook body for their own shop can replay it while swapping the shop-domain header to point at a different, victim shop, causing the app to process/attribute the event under the wrong tenant.

### Finding Description
`ShopifyApp::WebhookVerification` computes `hmac_valid?` strictly over `request.raw_post`: [1](#0-0) 

The `shop_domain` helper simply reads `HTTP_X_SHOPIFY_SHOP_DOMAIN` with no cryptographic binding to the signed body: [2](#0-1) 

The gem's own documentation instructs developers building custom webhook controllers to trust this unverified `shop_domain` value directly for tenant scoping when enqueuing background work: [3](#0-2) 

The design mirrors the root cause pattern in the external report: a piece of data that determines *whose* resources/identity an operation applies to (there: `_beneficiary`/`_from` in `Vault#addValue`; here: the shop the webhook payload is attributed to) is trusted from an input that is not restricted to only the legitimately-authorized party, while a separate, narrower check (there: `onlyMarket`/allowance; here: HMAC over body) is treated as sufficient authorization for the whole operation. Any Shopify merchant who installs the app is a valid, unprivileged sender of genuinely HMAC-signed webhook traffic for their own store (Shopify signs and delivers events triggered on that merchant's own shop). Nothing in `WebhookVerification` prevents that same merchant from capturing one of their own signed webhook deliveries and re-POSTing the identical (still validly-signed) body to the app's public webhook endpoint with the `X-Shopify-Shop-Domain` header changed to a different shop's domain. `verify_request` will pass because HMAC validity is unaffected by header changes, and `shop_domain` will now falsely report the victim shop.

### Impact Explanation
Any custom controller built per the documented pattern (`include ShopifyApp::WebhookVerification` + use `shop_domain` for job dispatch/tenant scoping) can have webhook-triggered background jobs executed against an arbitrary victim shop's records using data an attacker fully controls (from their own store's events), since the shop attribution is unauthenticated. This is a cross-shop write/data-integrity issue: an unrelated, unprivileged merchant can inject fabricated webhook events that the app believes originate from — and applies to — a shop they do not own or control.

### Likelihood Explanation
The webhook endpoint is a public, unauthenticated HTTP endpoint by design (Shopify calls it without any additional per-request user credential), so no privileged access is required to reach it. The only barrier is obtaining one validly HMAC-signed payload, which any merchant can trivially obtain by installing the app on their own store and triggering an event (e.g. `orders/create`). Forging/replaying with a modified `X-Shopify-Shop-Domain` header requires nothing beyond basic HTTP tooling.

### Recommendation
Do not derive tenant/shop identity from an unauthenticated header. Either:
- Include the shop domain in the HMAC-signed material (or otherwise cryptographically bind it to the payload) before trusting it, or
- Verify the `shop_domain` value against the app's own session/installation records (e.g., confirm an active session exists for that shop) before using it to scope any write/job dispatch, or
- Prefer the default `ShopifyAPI::Webhooks::Registry.process` path (backed by the shopify_api gem) instead of the documented custom pattern that trusts `shop_domain` directly, and update `docs/shopify_app/webhooks.md` to warn against using the unauthenticated `shop_domain` for authorization/tenant scoping.

### Proof of Concept
1. Attacker installs the target app on their own store `attacker.myshopify.com` and configures a webhook subscription (e.g. `orders/create`).
2. Attacker triggers an order-create event on their own store; Shopify delivers a webhook POST to the app with a body `B` and a correct `X-Shopify-Hmac-Sha256` computed over `B`, plus header `X-Shopify-Shop-Domain: attacker.myshopify.com`.
3. Attacker replays the exact same request to the app's custom webhook endpoint (per docs pattern using `ShopifyApp::WebhookVerification`), but sets `X-Shopify-Shop-Domain: victim.myshopify.com`.
4. `verify_request` in `lib/shopify_app/controller_concerns/webhook_verification.rb` recomputes the HMAC over the unchanged body `B` and it matches, so the request passes verification.
5. The custom controller calls `shop_domain`, which now returns `victim.myshopify.com`, and dispatches a job (e.g. `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`) that processes attacker-controlled payload data as if it belonged to the victim shop.

### Citations

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L13-26)
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
