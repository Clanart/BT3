### Title
App Proxy signature verification uses ambiguous array-to-string serialization, allowing signature reuse across semantically different query parameters - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#calculated_signature` serializes query parameters by joining array values with `,` before HMAC-signing, exactly mirroring the flaw pattern in the reported oracle bug: a compound (array) value is flattened into a byte/string representation that does not uniquely encode the original structure. Because `Rack::Utils.parse_query` can produce either a scalar string (e.g. `"1,2"`) or a multi-element array (e.g. `["1","2"]`) depending on how a query string is written, both are serialized identically as `"1,2"` before hashing, so a signature computed by Shopify for one representation is also accepted for a semantically different one.

### Finding Description
`query_string_valid?` and `calculated_signature` build the signed message like this: [1](#0-0) 

The critical line is: [2](#0-1) 

`query_hash_without_signature` comes from `Rack::Utils.parse_query(request.query_string)`. In Rack, `parse_query` builds an array value for a key only when that key appears **more than once** in the query string (`foo=1&foo=2` → `["1","2"]`); a single occurrence with an embedded comma (`foo=1,2`) instead yields the scalar string `"1,2"`. Both cases are passed through `Array(v).join(",")`, which collapses them to the identical string `"1,2"`. As a result, two structurally different query strings — one with `foo` as a scalar `"1,2"` and one with `foo` as the array `["1","2"]` — produce the exact same signed message and therefore the exact same valid HMAC signature.

This directly parallels the reported bug class: a compound-type value is converted into an encoding (here, a comma-joined string) that does not preserve the original array/scalar structure, so the verification step (`check_lps_updated` there, `calculated_signature`/`secure_compare` here) can be satisfied by two logically different inputs.

### Impact Explanation
Any legitimately Shopify-signed app-proxy URL that contains a query string whose signature was computed over `key=1,2` can be reused unmodified (signature unchanged) with `key` restructured into `key=1&key=2`. Downstream Rails controller code that reads `params[:key]` will now see an array of two elements instead of a single comma-containing string (or vice versa), while `verify_proxy_request`/`hmac_valid?`-style verification still reports the signature as valid. This is effectively an "accepted forged/altered signed request": an attacker who can observe or replay a previously issued signed app-proxy URL (these are not secret — they appear in browser history, referrers, logs, shared links, etc.) can alter the *interpreted* structure of one or more parameters without invalidating the signature, potentially changing application logic that branches on whether a parameter is a scalar or a list (e.g., ID filters, batch operation targets, pagination/sort keys) passed through the app proxy.

### Likelihood Explanation
Exploitation requires only observation of one previously signed app-proxy request URL (no secret knowledge, no privileged access) and simple query-string rewriting, which is trivial for an unauthenticated external actor. The vulnerable code path (`ShopifyApp::AppProxyVerification#verify_proxy_request`) is exercised by every controller that includes this concern, per the gem's documented usage. [3](#0-2) 

### Recommendation
Short term: when computing the signed message, do not rely on `Array(v).join(",")` for values that could have come from either a single scalar or a repeated key. Preserve and validate the exact key-occurrence structure (e.g., signing based on the raw ordered `[key, value]` pairs from the query string rather than a hash that has already collapsed repeats into arrays), or explicitly reject query strings where a decoded value contains the delimiter character used for joining (`,`) to avoid the collision. Long term: avoid depending on the specific reversible/ambiguous string form Rack produces for compound query parameters; treat verification data as raw bytes/exact pairs, and add tests covering the scalar-vs-array collision case (`foo=1,2` vs `foo=1&foo=2`) to ensure they cannot share a valid signature.

### Proof of Concept
1. Suppose Shopify issues (and signs) an app-proxy request with query string:
   `shop=my-shop.myshopify.com&foo=1,2&signature=<sig computed over "foo=1,2shop=my-shop.myshopify.com">`
2. An attacker observes this URL (e.g., via referrer/logs/shared link) and rewrites it, splitting `foo` into two occurrences while keeping the same `signature`:
   `shop=my-shop.myshopify.com&foo=1&foo=2&signature=<same sig>`
3. On the server, `Rack::Utils.parse_query` now produces `foo => ["1", "2"]` instead of `foo => "1,2"`.
4. `calculated_signature` computes `Array(["1","2"]).join(",")` = `"1,2"`, identical to the original `Array("1,2").join(",")` = `"1,2"`, so `secure_compare` still succeeds and `verify_proxy_request` accepts the request: [4](#0-3) 
5. The controller action now receives `params[:foo]` as a two-element array instead of the single string `"1,2"` that was actually signed, despite passing signature verification — demonstrating that the same valid signature accepts two semantically different payloads.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L1-13)
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
```

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L17-37)
```ruby
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
