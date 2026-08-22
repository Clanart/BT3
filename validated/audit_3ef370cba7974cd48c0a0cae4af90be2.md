### Title
`hmac_valid?` can raise `NoMethodError` instead of failing closed when `X-Shopify-Hmac-Sha256` header is absent, causing an unhandled 500 on `ExtensionVerificationController` routes - ([File: lib/shopify_app/controller_concerns/payload_verification.rb])

### Summary
`ShopifyApp::PayloadVerification#hmac_valid?` passes `shopify_hmac` (which is `nil` when the `X-Shopify-Hmac-Sha256` header is missing) directly into `ActiveSupport::SecurityUtils.secure_compare` as the first argument. `secure_compare` calls `.bytesize` on that argument before ever reaching the constant-time comparison, so a `nil` value raises `NoMethodError` rather than returning `false`. Since `ExtensionVerificationController#verify_request` does not rescue this, an unauthenticated POST without the HMAC header (or with an unsigned body) turns into an unhandled 500 instead of the intended `head(:unauthorized)`.

### Finding Description
`ExtensionVerificationController#verify_request` (app/controllers/shopify_app/extension_verification_controller.rb:11-16) is a `before_action` that calls `hmac_valid?(request.raw_post)` and expects a boolean to decide whether to `head(:unauthorized)`. `hmac_valid?` (lib/shopify_app/controller_concerns/payload_verification.rb:13-23) computes:

```ruby
secrets.any? do |secret|
  digest = OpenSSL::Digest.new("sha256")
  ActiveSupport::SecurityUtils.secure_compare(
    shopify_hmac,
    Base64.strict_encode64(OpenSSL::HMAC.digest(digest, secret, data)),
  )
end
```

`shopify_hmac` (line 9-11) reads `request.headers["HTTP_X_SHOPIFY_HMAC_SHA256"]`, which is `nil` when the attacker sends no such header. `ActiveSupport::SecurityUtils.secure_compare(a, b)` in Rails is implemented as `a.bytesize == b.bytesize && fixed_length_secure_compare(a, b)`; calling `.bytesize` on `nil` raises `NoMethodError: undefined method 'bytesize' for nil`. This exception is not caught anywhere in `verify_request` or `hmac_valid?`, so it propagates out of the `before_action` and becomes an unhandled 500 for any unauthenticated request that omits the HMAC header — the exact request an attacker with no API secret would send.

This only manifests when `ShopifyApp.configuration.secret` (and/or `old_secret`) is present (the normal, expected production configuration), since `secrets` is only non-empty then and `secrets.any?` only invokes the block — and thus `secure_compare` — when there is at least one configured secret. In the typical deployed app this precondition is always true, so the path is reachable by an unauthenticated attacker without any special access.

### Impact Explanation
This is an unauthenticated denial-of-service / unhandled-exception condition on a public route (`ExtensionVerificationController` and its subclasses, e.g. checkout/POS UI extension verification endpoints). It maps to Shopify's "Denial of Service" / improper error handling impact class rather than data or token compromise — no session, token, or cross-shop data is exposed, but the fail-closed invariant (verification errors must reject the request cleanly) is violated, producing 500s that could be used to probe or disrupt the service and pollute error monitoring/logs.

### Likelihood Explanation
Highly likely/trivial to trigger: any anonymous client can `POST` to a route mounted on a subclass of `ExtensionVerificationController` without setting `X-Shopify-Hmac-Sha256`. No secret, token, or prior session is required. It is fully repeatable and deterministic given a normal (non-blank) `secret` configuration, which is the standard deployment condition.

### Recommendation
Guard against a missing header before calling `secure_compare`, e.g.:
```ruby
def hmac_valid?(data)
  return false if shopify_hmac.blank?

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
Alternatively/additionally, wrap the comparison in a `begin/rescue` that fails closed (`false`) on any `StandardError`, and apply the same fix pattern anywhere else `secure_compare` is fed a potentially-nil header value.

### Proof of Concept
```ruby
class DummyExtensionController < ShopifyApp::ExtensionVerificationController
  def create
    head :ok
  end
end

class ExtensionVerificationControllerTest < ActionController::TestCase
  tests DummyExtensionController

  setup do
    ShopifyApp.configure { |c| c.secret = "sekret" }
  end

  test "missing HMAC header should return 401, not raise" do
    with_routing do |set|
      set.draw { post "/extension" => "dummy_extension#create" }
      # No X-Shopify-Hmac-Sha256 header set at all
      post :create, body: "A" * 5_000_000  # large/binary-ish raw body
      assert_response :unauthorized   # currently raises NoMethodError instead
    end
  end
end
```
Expected (buggy) behavior: `NoMethodError: undefined method 'bytesize' for nil` raised from `ActiveSupport::SecurityUtils.secure_compare`, surfaced as an unhandled 500. Expected (fixed) behavior: `head(:unauthorized)` and HTTP 401. [1](#0-0) [2](#0-1)

### Citations

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

**File:** lib/shopify_app/controller_concerns/payload_verification.rb (L1-25)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module PayloadVerification
    extend ActiveSupport::Concern

    private

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
  end
end
```
