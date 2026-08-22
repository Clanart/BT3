Confirmed: `ShopifyApp::AppProxyVerification#query_string_valid?` includes a `timestamp` query parameter in the HMAC-signed data, but the module never validates that the timestamp is recent — it only checks that the HMAC matches. This is the direct in-repo analog to the reported "lack of deadline check" bug class. [1](#0-0) 

### Title
Missing timestamp/deadline validation allows indefinite replay of signed App Proxy requests - (File: lib/shopify_app/controller_concerns/app_proxy_verification.rb)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` verifies that the `signature` query parameter matches an HMAC computed over the other query parameters (including a Shopify-supplied `timestamp`), but it never checks that `timestamp` is recent. [2](#0-1)  Any request URL that was ever validly signed by Shopify for a given app proxy path remains permanently replayable, since the module only performs a constant-time comparison of the computed vs. supplied HMAC digest with no expiry/window check.

### Finding Description
The `calculated_signature` method folds the `timestamp` parameter into the signed data along with all other query params, but `query_string_valid?` treats a matching signature as sufficient proof of authenticity regardless of how old that timestamp is. [3](#0-2)  This is structurally identical to the reported `Forwarder` bug class: a signed payload intended to represent a point-in-time authorization from Shopify (via the app proxy) can be captured once (e.g., from browser history, a referrer header on an outbound link, shared/cached URLs, proxy/CDN logs, or a man-in-the-middle on a network without TLS pinning) and then replayed by any unprivileged actor at any point in the future — even years later — since there is no deadline/expiry logic anywhere in the request-verification path. [4](#0-3) 

Every generated and documented usage pattern for app proxy controllers relies solely on this module for authorization of the incoming request, with no supplementary freshness check added by the templates. [5](#0-4)  The accompanying documentation also only describes signature validity, not freshness. [6](#0-5) 

### Impact Explanation
Because the app-proxy signature is the sole authentication mechanism for these routes (no session/cookie required, since app proxy requests are storefront-originated), a captured signed URL can be replayed indefinitely by an anonymous or unrelated party to invoke app-proxy endpoints under the originally-targeted shop's identity, at a time when the original execution context (cart contents, customer session, business logic assumptions) may have completely changed. For any app proxy endpoint that performs a state-changing action keyed off these query parameters (as opposed to pure read-only rendering), this enables replay-based abuse. This matches the accepted vulnerability class of "accepted forged/stale signed request" from the rules, since the request is cryptographically valid but temporally illegitimate.

### Likelihood Explanation
Exploitation requires an attacker to first obtain one valid, signed app-proxy URL — via referrer leakage, browser history, shared links, logs, or interception — which is plausible in numerous real-world scenarios (storefronts frequently link out, embed the proxy URL in page markup, or expose it via `Referer` headers to third-party resources loaded on the page). Once obtained, replay is trivial and unlimited since there is no time-boxing at all.

### Recommendation
Add a timestamp-freshness check in `query_string_valid?` (or a wrapping `before_action`) that rejects requests where `Time.now.to_i - query_hash["timestamp"].to_i` exceeds a small tolerance window (e.g., a few minutes), mirroring the recommended mitigation from the original report of introducing deadline/expiry validation for signed payloads.

### Proof of Concept
1. A storefront customer's browser loads an app-proxy-rendered page; the resulting fully-signed URL (`shop=...&path_prefix=...&timestamp=...&signature=...`) is captured (e.g., via a bookmark, shared link, or `Referer` leakage to an embedded third-party resource).
2. Any time later — arbitrarily far in the future — an unrelated party issues the exact same GET request to the app-proxy route.
3. `verify_proxy_request` calls `query_string_valid?`, which recomputes the HMAC over the identical parameters (including the now-stale `timestamp`) and finds it matches, so the request passes verification and is processed as if freshly issued by Shopify. [1](#0-0)

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L1-27)
```ruby
# frozen_string_literal: true

module ShopifyApp
  module AppProxyVerification
    extend ActiveSupport::Concern
    included do
      skip_before_action :verify_authenticity_token, raise: false
      before_action :verify_proxy_request
    end

    def verify_proxy_request
      head(:forbidden) unless query_string_valid?(request.query_string)
    end

    private

    def query_string_valid?(query_string)
      query_hash = Rack::Utils.parse_query(query_string)

      signature = query_hash.delete("signature")
      return false if signature.nil?

      ActiveSupport::SecurityUtils.secure_compare(
        calculated_signature(query_hash),
        signature,
      )
    end
```

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L29-37)
```ruby
    def calculated_signature(query_hash_without_signature)
      sorted_params = query_hash_without_signature.collect { |k, v| "#{k}=#{Array(v).join(",")}" }.sort.join

      OpenSSL::HMAC.hexdigest(
        OpenSSL::Digest.new("sha256"),
        ShopifyApp.configuration.secret,
        sorted_params,
      )
    end
```

**File:** lib/generators/shopify_app/app_proxy_controller/templates/app_proxy_controller.rb (L1-9)
```ruby
# frozen_string_literal: true

class AppProxyController < ApplicationController
  include ShopifyApp::AppProxyVerification

  def index
    render(layout: false, content_type: "application/liquid")
  end
end
```

**File:** docs/shopify_app/engine.md (L52-61)
```markdown
## Verify HTTP requests sent via an app proxy

See [`ShopifyApp::AppProxyVerification`](/lib/shopify_app/controller_concerns/app_proxy_verification.rb).

The engine provides a mixin for verifying incoming HTTP requests sent via an App Proxy. Any controller that `include`s `ShopifyApp::AppProxyVerification` will verify that each request has a valid `signature` query parameter that is calculated using the other query parameters and the app's shared secret.

### Recommended usage of `ShopifyApp::AppProxyVerification`

The App Proxy Controller Generator automatically adds the mixin to the generated app_proxy_controller.rb
Additional controllers for resources within the App_Proxy namespace, will need to include the mixin like so:
```
