## Title
CSRF protection bypass via unverified `id_token` param — attacker-supplied JWT skips forgery-token check without signature validation - ([File: lib/shopify_app/controller_concerns/csrf_protection.rb])

## Summary
`ShopifyApp::CsrfProtection` disables Rails' `protect_from_forgery` whenever `valid_session_token?` returns true, and that predicate is satisfied merely by `jwt_payload.present?` — i.e., by the mere presence of a parseable id token, not by cryptographic verification of it at that point in the request lifecycle.

## Finding Description
`CsrfProtection#valid_session_token?` calls `jwt_payload.present?` [1](#0-0)  which is defined in `ShopifyApp::WithShopifyIdToken#jwt_payload`, constructing a `ShopifyAPI::Auth::JwtPayload` from whatever token is found in the `Authorization` header or, notably, the `id_token` URL/body parameter [2](#0-1) [3](#0-2) . The concern is wired so that this same "is a token present/parseable" check is what governs whether Rails' `protect_from_forgery with: :exception` runs at all: `protect_from_forgery with: :exception, unless: :valid_session_token?` [4](#0-3) .

This mirrors the structure of the zkSync report's root cause: a privileged/guarded action (`updateNonceOrdering`) is properly restricted on one call path (the `isSystemCall` selector check inside the default account's `_execute`), but a second, less strict path (raw L1 priority transaction) only checks a coarser condition (`to == CONTRACT_DEPLOYER_ADDR()`) and skips the stricter check, so the restriction is effectively bypassed. Here, the "restriction" is CSRF protection, and it is bypassed if the coarse condition `jwt_payload.present?` is satisfiable without the strict, cryptographically verifying check that is performed elsewhere in the codebase (e.g., in `ShopifyAPI::Utils::SessionUtils.current_session_id` used by `LoginProtection#load_current_session`, or in `ShopifyApp::Auth::TokenExchange.perform`, both of which are separate code paths not exercised by `CsrfProtection`).

I could not fully confirm from this repo's index whether `ShopifyAPI::Auth::JwtPayload.new` (defined in the separate `shopify_api` gem, not vendored in this repo) validates the JWT signature synchronously at construction time or only decodes it, deferring signature/audience verification to later API calls. This is the key unresolved fact: if `JwtPayload.new` raises `ShopifyAPI::Errors::InvalidJwtTokenError` on a forged/invalid signature, `jwt_payload` becomes `nil` and `valid_session_token?` is `false`, so CSRF protection remains active and there is no bypass. If, however, `JwtPayload.new` only decodes/parses the JWT structure (checking claims like expiry format) without verifying the HS256 signature against the app's client secret at that point, then any attacker who can guess/derive the `aud`/`dest`/`sub` claim format could submit an arbitrary unsigned or badly-signed `id_token` param on a same-site or cross-site POST and have `protect_from_forgery` skipped entirely, defeating CSRF protection for any controller including `ShopifyApp::CsrfProtection` (which is required by `TokenExchange` and `EnsureHasSession`) [5](#0-4) .

## Impact Explanation
If confirmed, this would let an attacker mount a CSRF attack against any state-changing endpoint in an app using `ShopifyApp::EnsureHasSession`/`TokenExchange` (which include `CsrfProtection`), simply by adding an `id_token` parameter to a forged cross-site request, without needing a valid signed session token. That would defeat the CSRF token check for authenticated actions on the app.

## Likelihood Explanation
Uncertain/Low-Medium — contingent entirely on the unverified external behavior of `ShopifyAPI::Auth::JwtPayload.new`. This class lives in the `shopify_api` Ruby gem dependency, not in this repository's index, so I could not verify signature validation happens (or doesn't happen) at construction time. Given the test suite shows `valid_session_token?` tests using well-formed, correctly-signed JWTs [6](#0-5) , it's plausible the gem does perform signature verification in the constructor, in which case there is no real bypass.

## Recommendation
Verify (in the `shopify_api` gem source, which is out of scope for this index) whether `ShopifyAPI::Auth::JwtPayload.new` cryptographically verifies the token signature at construction. If it does not, `CsrfProtection#valid_session_token?` should be changed to only skip forgery protection when the token has been fully verified (signature, audience, expiry) against the configured API secret/key — not merely parsed — mirroring the recommendation from the report to not let a weaker validation path substitute for the stronger one that governs the security-critical restriction.

## Proof of Concept
Not able to construct a concrete PoC without confirming the external gem's verification behavior; this is a conditional/unconfirmed finding pending inspection of `ShopifyAPI::Auth::JwtPayload#initialize` in the `shopify_api` gem.

### Citations

**File:** lib/shopify_app/controller_concerns/csrf_protection.rb (L6-9)
```ruby
    included do
      include ShopifyApp::WithShopifyIdToken
      protect_from_forgery with: :exception, unless: :valid_session_token?
    end
```

**File:** lib/shopify_app/controller_concerns/csrf_protection.rb (L13-15)
```ruby
    def valid_session_token?
      jwt_payload.present?
    end
```

**File:** lib/shopify_app/controller_concerns/with_shopify_id_token.rb (L7-17)
```ruby
    def shopify_id_token
      return @shopify_id_token if defined?(@shopify_id_token)

      @shopify_id_token = id_token_from_authorization_header || id_token_from_url_param
    end

    def jwt_payload
      return @jwt_payload if defined?(@jwt_payload)

      @jwt_payload = shopify_id_token.present? ? ShopifyAPI::Auth::JwtPayload.new(shopify_id_token) : nil
    end
```

**File:** lib/shopify_app/controller_concerns/with_shopify_id_token.rb (L40-46)
```ruby
    def id_token_from_authorization_header
      request.headers["HTTP_AUTHORIZATION"]&.match(/^Bearer (.+)$/)&.[](1)
    end

    def id_token_from_url_param
      params["id_token"]
    end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L1-12)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module TokenExchange
    extend ActiveSupport::Concern
    include ShopifyApp::AdminAPI::WithTokenRefetch
    include ShopifyApp::SanitizedParams
    include ShopifyApp::EmbeddedApp

    included do
      include ShopifyApp::WithShopifyIdToken
    end
```

**File:** test/shopify_app/controller_concerns/csrf_protection_test.rb (L48-60)
```ruby
  test "it does not raise if a valid session token was provided" do
    jwt_payload = {
      iss: "iss",
      dest: "https://test-shop.myshopify.com",
      aud: ShopifyAPI::Context.api_key,
      sub: "1",
      exp: (Time.now + 10).to_i,
      nbf: 1234,
      iat: 1234,
      jti: "4321",
      sid: "abc123",
    }
    jwt_token = JWT.encode(jwt_payload, ShopifyAPI::Context.api_secret_key, "HS256")
```
