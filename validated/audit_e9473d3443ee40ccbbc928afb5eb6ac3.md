### No vulnerability found for this question.

The code at `lib/shopify_app/controller_concerns/app_proxy_verification.rb` explicitly rejects requests with a missing `signature`: `query_string_valid?` returns `false` immediately when `signature.nil?`, which causes `verify_proxy_request` to respond `head(:forbidden)` and block the request rather than accept it. [1](#0-0) 

When a signature is present, the code recomputes the HMAC over the sorted, comma-joined parameter set and compares it to the supplied signature using `ActiveSupport::SecurityUtils.secure_compare`, a constant-time comparison, which prevents forged signatures from being accepted without knowledge of `ShopifyApp.configuration.secret`. [2](#0-1) 

An attacker with no access to `ShopifyApp.configuration.secret` cannot construct a query string (with or without a `signature` param, with any repeated/array param shape) that would satisfy `secure_compare` against the correctly recomputed HMAC, and an absent signature is explicitly denied rather than bypassed. This is the intended, secure control-flow — the early `return false` is a rejection path, not an authentication bypass — so no exploitable forged app-proxy acceptance exists here.

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
