Based on my investigation of `blackvul/shopify_app--022`, I found a valid analog vulnerability in the webhook-verification flow.

### Title
Webhook shop identity (`shop_domain`) is not covered by HMAC verification, allowing cross-shop webhook spoofing - (File: `lib/shopify_app/controller_concerns/webhook_verification.rb`)

### Summary
`ShopifyApp::WebhookVerification#verify_request` authenticates an incoming webhook purely by recomputing an HMAC over the raw request body and comparing it to the `X-Shopify-Hmac-Sha256` header. The shop-identifying value that custom webhook controllers are told to trust, `shop_domain`, is read straight from the `X-Shopify-Shop-Domain` request header, which is **not** part of the HMAC-signed data. This mirrors the reported bug class: an attacker-controlled identity value (`sender` in the Notional report; `shop_domain` here) is accepted as authoritative even though only a different, unrelated piece of data (`sender == address(this)` check; the raw body HMAC) was actually verified.

### Finding Description
`hmac_valid?` computes the digest over `request.raw_post` only: [1](#0-0) 

`verify_request` gates the action solely on that body HMAC: [2](#0-1) 

But the module also exposes `shop_domain`, taken directly from an unauthenticated header: [3](#0-2) 

The gem's own documentation instructs developers building custom webhook controllers to trust this unverified value as the shop context for background job processing: [4](#0-3) 

Because the app's webhook-signing secret (`ShopifyApp.configuration.secret`) is a single app-level secret shared across **every** shop that installs the app (it is the app's client secret, not a per-shop value), any entity that installs the app on their own shop receives webhook deliveries validly signed with that same shared secret. That party can then replay the exact raw body (and therefore a still-valid HMAC) directly to the app's webhook endpoint while substituting an arbitrary `X-Shopify-Shop-Domain` header, since the header is never part of what `hmac_valid?` checks. The value returned by `verify_request` establishes only "this body was signed by the app's secret at some point for some shop" — not "this request truly originated for `shop_domain`". Any controller/job built on top of the documented pattern therefore accepts a forged shop-identity value for processing, exactly analogous to `notionalCallback` accepting attacker-supplied `sender` instead of verifying the true caller.

### Impact Explanation
A merchant/developer who installs the target app on any shop (an "unrelated merchant") can forge webhook requests that appear to originate from a different, victim shop that also has the app installed. Depending on what the job driven by `shop_domain` does (e.g., mandatory privacy webhooks such as `customers/data_request`, `customer/redact`, `shop/redact`, or any shop-scoped data-mutating webhook job), this enables cross-shop data manipulation, unauthorized data deletion/export requests, or injection of attacker-controlled payload data into another shop's processing pipeline — a cross-shop access/data-integrity issue satisfying "accepted forged signed request" / "cross-shop access."

### Likelihood Explanation
Exploitation requires only: (1) legitimately installing the app once (no special privilege, any Partner/dev/test store qualifies), (2) capturing one real webhook delivery (trivial, since apps must expose an endpoint reachable from the internet), and (3) sending a direct HTTP POST to that same endpoint with a modified `X-Shopify-Shop-Domain` header. No secret leakage or insider access is needed beyond the normal app-install flow, making this a realistic, low-effort exploitation path for any external, unrelated merchant.

### Recommendation
Do not trust `X-Shopify-Shop-Domain` (or any other webhook header) as an authenticated identity. Either:
- Include the shop domain/id inside the HMAC-covered payload and validate it against the header/derived shop before use, or
- Cross-check the header value against an existing, previously-established session/shop record (e.g., confirm a stored session exists for that shop and that the webhook's own body content, if it encodes shop identity, agrees with the header) before passing `shop_domain` onward to any job, and document this requirement prominently instead of recommending the current unguarded pattern.

### Proof of Concept
1. Attacker installs the target Shopify app on `attacker-shop.myshopify.com` (legitimate, unprivileged install).
2. Shopify sends a real webhook, e.g. `carts/update`, to the app's custom endpoint with body `B`, header `X-Shopify-Hmac-Sha256: H` (valid, computed with the shared app secret over `B`), and `X-Shopify-Shop-Domain: attacker-shop.myshopify.com`.
3. Attacker resends the identical `B`/`H` pair directly to the same endpoint, but sets `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
4. `verify_request` (`lib/shopify_app/controller_concerns/webhook_verification.rb:15-21`) recomputes the HMAC over `B` only — it matches, so the request passes.
5. The custom controller (built per the documented pattern in `docs/shopify_app/webhooks.md:86-104`) calls `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`, where `shop_domain` now resolves to `victim-shop.myshopify.com`, causing attacker-controlled webhook data to be processed under the victim shop's identity.

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
