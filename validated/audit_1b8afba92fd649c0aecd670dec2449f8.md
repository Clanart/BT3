### Title
Webhook shop-domain attribution is not covered by HMAC verification, enabling cross-shop request spoofing - ([File: lib/shopify_app/controller_concerns/webhook_verification.rb])

### Summary
`ShopifyApp::WebhookVerification` authenticates only the raw request **body** via HMAC-SHA256, but the shop attribution value used by downstream job/controller code — `shop_domain`, sourced from the `X-Shopify-Shop-Domain` header — is never included in, or cross-checked against, that signature. Any request whose body+HMAC pair is valid for the shared app secret will pass `verify_request` regardless of which shop the `X-Shopify-Shop-Domain` header claims to be, letting an attacker with a valid signed payload (e.g. from their own installed shop) attribute the processed data to an arbitrary shop.

### Finding Description
`WebhookVerification#verify_request` computes the HMAC exclusively over `request.raw_post` and compares it to the `X-Shopify-Hmac-Sha256` header using the app's shared secret(s): [1](#0-0) [2](#0-1) 

The `shop_domain` helper that the gem documents and ships for custom webhook controllers reads directly from the unsigned `X-Shopify-Shop-Domain` header: [3](#0-2) 

The documented usage pattern explicitly passes this unauthenticated value into background job processing that performs shop-scoped work: [4](#0-3) 

Because the shared app secret is the same for every shop that installs a given app, and the HMAC only proves "this body was signed with the app secret" rather than "this body/header pair was delivered by Shopify for shop X," an attacker who controls (or has intercepted) any single validly-signed webhook body for the app — trivially obtainable by triggering an event on their own installed shop — can replay that exact body/HMAC pair to the app's webhook endpoint while substituting an arbitrary value in `X-Shopify-Shop-Domain`. The signature check still passes because it never inspects that header, and the app then executes shop-scoped logic (`shop_domain:` passed to jobs) under the attacker-chosen tenant identity instead of the shop that actually produced the signed payload.

This mirrors the referenced bug class precisely: a value that is supposed to represent a previously committed/authenticated identity (`fixed_side_capacity`/ticks in the vault, `shop_domain` here) is instead accepted from attacker-controlled input and used unchecked for the privileged downstream action (minting the sole claim token / dispatching shop-scoped background work), because the validation step that should bind the two together was never enforced.

### Impact Explanation
An attacker who is a legitimate but unrelated merchant of a multi-tenant app can cause the app to process webhook data under a different shop's identity, corrupting per-shop records, triggering shop-scoped side effects (e.g. re-triggering mandatory GDPR jobs, script tag/webhook management, or any custom job keyed by `shop_domain`) attributed to a victim shop they do not control. This is a cross-tenant confused-deputy condition reachable from an anonymous/attacker-controlled HTTP request bearing only a previously-obtained valid signed body.

### Likelihood Explanation
Medium: exploitation requires the attacker to obtain one validly signed webhook body (trivial — they can install the app on their own shop and trigger any webhook event themselves) and then send a crafted HTTP request directly to the app's public webhook endpoint with a modified `X-Shopify-Shop-Domain` header. No secret knowledge or privileged access to another tenant is required.

### Recommendation
1. Do not treat `X-Shopify-Shop-Domain` as trusted unless it is cryptographically bound to the signed payload (e.g., derive/verify the shop from data inside the HMAC-covered body, or additionally verify the shop against an installed-session lookup before dispatching shop-scoped work).
2. Reject webhook requests where the shop implied by the header cannot be corroborated against a known, previously authenticated installation for that exact webhook/topic.
3. Document and enforce that `shop_domain` from `WebhookVerification` must never be used as an authorization or tenant-selection value without additional server-side corroboration, consistent with the `requested_shopify_domain` vs `authenticated_shopify_domain` separation already established for token exchange (`lib/shopify_app/controller_concerns/token_exchange.rb`).

### Proof of Concept
1. Install the target app on an attacker-controlled shop `attacker.myshopify.com`; trigger any subscribed webhook topic (e.g. `app/uninstalled`) to receive one legitimately signed request with body `B` and header `X-Shopify-Hmac-Sha256: H = HMAC-SHA256(secret, B)`.
2. Replay the request directly to the app's webhook endpoint, keeping body `B` and header `H` unchanged, but set `X-Shopify-Shop-Domain: victim-shop.myshopify.com`.
3. `WebhookVerification#verify_request` recomputes `HMAC-SHA256(secret, B)`, which still equals `H`, so the request is accepted: [5](#0-4) 
4. The controller/job reads `shop_domain` from the spoofed header and performs shop-scoped processing (e.g. `SomeJob.perform_later(shop_domain: shop_domain, webhook: webhook_params.to_h)`) attributed to `victim-shop.myshopify.com`, even though the payload never originated from that shop: [6](#0-5)

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
