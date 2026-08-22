## Title
App Proxy signed-request replay due to missing timestamp/freshness and one-time-use check on the HMAC signature - (File: lib/shopify_app/controller_concerns/app_proxy_verification.rb)

### Summary
`ShopifyApp::AppProxyVerification#query_string_valid?` validates an inbound App Proxy request purely by recomputing an HMAC over the sorted query parameters (which includes a `timestamp` value) and comparing it to the `signature` parameter [1](#0-0) . The `timestamp` field is treated only as opaque signed data, never checked against the current time or any "already used" store, so a signature that was valid once remains valid forever and can be replayed verbatim any number of times.

### Finding Description
The concern only performs a constant-time comparison of the HMAC digest: [2](#0-1) 

There is no logic anywhere in the gem that:
- Rejects a `timestamp` older than some allowed skew.
- Tracks or invalidates a signature/nonce after first use.

This is structurally the same root cause as the referenced finding: the verification checks that *the signed data is authentic*, but never checks that *this specific signed instance hasn't already been consumed / isn't stale*. In the original bug, `SmartAccount` checked `nonces[batchId]` but never bound `batchId` into the hash, so a valid signature could be re-submitted under a different (already-zero-nonce) `batchId`. Here, `query_string_valid?` checks the HMAC over the whole query string (which happens to include `timestamp`), but never verifies that the `timestamp` is recent or that the exact `(query_string, signature)` pair hasn't been processed before — so the identical valid request can be resent indefinitely.

The `WebhookVerification`/`PayloadVerification` HMAC check has the same structural gap (no `X-Shopify-Webhook-Id` dedupe or delivery-time freshness check) [3](#0-2) [4](#0-3) , but the App Proxy path is the more directly reachable one from an anonymous/unrelated party, since App Proxy URLs are rendered into storefront pages (and thus can leak via Referer headers, browser history, shared links, proxies, or logs) rather than requiring server-to-server interception.

### Impact Explanation
Any App Proxy controller built on `ShopifyApp::AppProxyVerification` (e.g. the generated `AppProxyController` [5](#0-4) ) will accept a captured, previously-valid signed request from an anonymous actor at any point in the future. If the underlying action is not idempotent (e.g. submitting a form, creating a review, redeeming a discount/coupon, incrementing a counter), an attacker can trigger the state-changing action repeatedly by simply resending the captured URL — a classic accepted-forged/replayed signed request leading to cross-user/cross-shop unauthorized state changes, without needing any shop credentials or session.

### Likelihood Explanation
Likelihood is moderate to high in any app that performs mutating operations behind an App Proxy route: App Proxy GET/POST URLs containing the `signature` and `timestamp` query params are exposed directly to the storefront visitor's browser (they are not a secret channel), making capture trivial via network inspection, Referer leakage, or simple curiosity. Since the gem enforces no expiry window and no anti-replay store, every captured URL remains exploitable indefinitely.

### Recommendation
- Reject requests where `timestamp` is older than a small allowed window (e.g. a few minutes), similar to Shopify's guidance for OAuth/webhook staleness checks.
- Optionally require and track a single-use nonce/request identifier server-side (e.g. via `SessionRepository`-style storage or a short-TTL cache) so a given signed request cannot be processed twice.
- Document this limitation prominently for App Proxy adopters, since currently `query_string_valid?` gives the false impression of full replay protection.

### Proof of Concept
1. Configure an App Proxy-backed controller with `include ShopifyApp::AppProxyVerification` performing a state-changing action (e.g. creating a record) on `POST`.
2. Capture one legitimately Shopify-issued proxy request (`shop`, `path_prefix`, `timestamp`, `signature`, plus action params) from network traffic/logs.
3. Replay the exact same request (same query string and `signature`) any number of times, including well after the original timestamp.
4. Observe that `query_string_valid?` returns `true` every time [6](#0-5) , and the mutating action executes repeatedly — mirroring the original PoC's repeated `execTransaction` replay via reused nonce/batchId.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L11-37)
```ruby
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

    def calculated_signature(query_hash_without_signature)
      sorted_params = query_hash_without_signature.collect { |k, v| "#{k}=#{Array(v).join(",")}" }.sort.join

      OpenSSL::HMAC.hexdigest(
        OpenSSL::Digest.new("sha256"),
        ShopifyApp.configuration.secret,
        sorted_params,
      )
    end
```

**File:** lib/shopify_app/controller_concerns/webhook_verification.rb (L13-21)
```ruby
    private

    def verify_request
      data = request.raw_post
      unless hmac_valid?(data)
        ShopifyApp::Logger.debug("Webhook verification failed - HMAC invalid")
        head(:unauthorized)
      end
    end
```

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
