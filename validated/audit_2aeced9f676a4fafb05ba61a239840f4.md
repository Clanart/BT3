### Title
HMAC verification lacks context binding, allowing signature reuse across unrelated endpoints - (File: `lib/shopify_app/controller_concerns/payload_verification.rb`)

### Summary
`ShopifyApp::PayloadVerification#hmac_valid?` computes and compares an HMAC-SHA256 over the raw request body and the app's shared secret only, with no binding to the destination endpoint, webhook topic, or intended action. This same verification logic is shared verbatim by `ShopifyApp::WebhookVerification` (used by `WebhooksController` and any custom/generated webhook controllers for arbitrary topics) and `ShopifyApp::ExtensionVerificationController` (used for extension-style callbacks such as marketing activity extensions). Because the signature does not encode which topic/action/endpoint it was issued for, a signed payload legitimately produced for one context can be replayed against any other controller in the app that includes the same verification concern, as long as the raw body is accepted by that controller's parsing logic.

### Finding Description
`hmac_valid?` in `lib/shopify_app/controller_concerns/payload_verification.rb:13-23` computes `Base64.strict_encode64(OpenSSL::HMAC.digest(sha256, secret, data))` where `data` is `request.raw_post`, and compares it to the `X-Shopify-Hmac-Sha256` header using `secure_compare`. Nothing else — no topic name, no controller/action identifier, no route — is folded into the signed material.

This concern is included identically by:
- `ShopifyApp::WebhookVerification` (`lib/shopify_app/controller_concerns/webhook_verification.rb:1-27`), used by `ShopifyApp::WebhooksController` (`app/controllers/shopify_app/webhooks_controller.rb:1-16`) and by any app-generated webhook controller (`lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt:1-13`).
- `ShopifyApp::ExtensionVerificationController` (`app/controllers/shopify_app/extension_verification_controller.rb:1-18`), which is a separate, differently-purposed base controller (e.g., for marketing activity extension callbacks) using the exact same `hmac_valid?(request.raw_post)` check.

Both consumers validate solely "was this body signed by our shared secret?" — they never assert "was this body signed *for this specific endpoint/topic*?" This is the same root cause pattern as the external report: a signature scheme without a predefined, context-restricting component allows a signature valid in one operation's context to be accepted as valid in a different operation's context, because the verifier cannot distinguish intended use from incidental structural similarity.

### Impact Explanation
If an attacker can obtain (e.g., by controlling data fields Shopify includes verbatim in a webhook body, or by intercepting/observing a legitimate webhook delivery) a validly-HMAC-signed raw body for one topic/endpoint, that exact `(body, hmac)` pair remains valid for any other controller in the same app that also includes `WebhookVerification` or inherits `ExtensionVerificationController`, since both perform identical unscoped verification. This can let a payload validly signed for a harmless/low-privilege topic be replayed to trigger processing on an unrelated, higher-impact webhook job or extension action, as long as the receiving controller's parsing of that body coincidentally satisfies its expectations. This is a cross-context accepted forged/misdirected signed request, matching the "accepted forged signed request" acceptance criterion.

### Likelihood Explanation
Exploitation likelihood is moderate to low in the general case because the attacker still needs a body that is simultaneously (a) validly signable/obtainable and (b) meaningful to the other endpoint's business logic — this is analogous to the original report's reliance on an admin signing a request without noticing repurposed content. It does not require any secret leakage, only structural signature-scope confusion inherent to the library's verification design, which is present for every app built on this gem's webhook/extension verification concerns.

### Recommendation
Bind the HMAC input to the specific context it is intended for. For example, incorporate the target endpoint identity (webhook topic, controller/action name, or route) into the signed/verified data, or verify a scope claim within the payload against the current controller/action before accepting the HMAC as valid, rather than relying purely on `secret + raw body`. Ensure `WebhookVerification` and `ExtensionVerificationController` cannot silently accept a body/signature pair intended for a different consumer.

### Proof of Concept
Conceptual PoC (cannot be fully demonstrated without the app secret, which is out of scope to obtain):
1. Attacker captures/derives a raw body `B` and its corresponding valid `X-Shopify-Hmac-Sha256` value `H` for topic `carts/update` delivered to `WebhooksController`.
2. Attacker crafts or waits for `B` to also be parseable/meaningful by a controller inheriting `ExtensionVerificationController` (or a differently-purposed webhook controller sharing the same secret).
3. Attacker POSTs `B` with header `H` to that unrelated endpoint.
4. `verify_request` in `webhook_verification.rb` / `extension_verification_controller.rb` calls `hmac_valid?(B)`, which only checks `secret + B` — it succeeds because nothing in the check restricts `H` to the original topic/endpoint, and the unrelated action executes. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L1-21)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module WebhookVerification
    extend ActiveSupport::Concern
    include ShopifyApp::PayloadVerification

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

**File:** app/controllers/shopify_app/extension_verification_controller.rb (L1-18)
```ruby
# frozen_string_literal: true

module ShopifyApp
  class ExtensionVerificationController < ActionController::Base
    include ShopifyApp::PayloadVerification
    protect_from_forgery with: :null_session
    before_action :verify_request

    private

    def verify_request
      unless hmac_valid?(request.raw_post)
        head(:unauthorized)
        ShopifyApp::Logger.debug("Extension verification failed due to invalid HMAC")
      end
    end
  end
end
```

**File:** app/controllers/shopify_app/webhooks_controller.rb (L1-16)
```ruby
# frozen_string_literal: true

module ShopifyApp
  class WebhooksController < ActionController::Base
    include ShopifyApp::WebhookVerification

    def receive
      params.permit!

      ShopifyAPI::Webhooks::Registry.process(
        ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h),
      )
      head(:ok)
    end
  end
end
```

**File:** lib/generators/shopify_app/add_declarative_webhook/templates/webhook_controller.rb.tt (L1-13)
```text
# frozen_string_literal: true

module Webhooks
  class <%= @controller_class_name %> < ApplicationController
    include ShopifyApp::WebhookVerification

    def receive
      webhook_request = ShopifyAPI::Webhooks::Request.new(raw_body: request.raw_post, headers: request.headers.to_h)
      <%= @job_class_name %>.perform_later(shop_domain: webhook_request.shop, webhook: webhook_request.parsed_body)
      head(:no_content)
    end
  end
end
```
