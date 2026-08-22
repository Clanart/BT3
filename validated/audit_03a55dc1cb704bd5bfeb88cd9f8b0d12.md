### Title
App proxy signature verification is malleable via ambiguous parameter re-serialization (`sorted_params` boundary collision) - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#calculated_signature` reconstructs the signed string by concatenating `"key=value"` pairs with no delimiter between pairs and joining multi-valued params with a bare comma, without escaping `=` or `,` inside decoded values. Because `Rack::Utils.parse_query` has already URL-decoded the query string, an attacker who controls the raw value of a single parameter can make the serialized string for a one-key request byte-identical to that of a different, multi-key request, causing `calculated_signature` (and thus `query_string_valid?`) to accept a request whose parameter structure was never actually signed by Shopify.

### Finding Description
`calculated_signature` builds the string to HMAC as: [1](#0-0) 
`query_hash_without_signature.collect { |k, v| "#{k}=#{Array(v).join(",")}" }.sort.join` — note the final `.join` has no separator, so distinct `"key=value"` fragments are simply concatenated back-to-back with nothing between them, and `=`/`,` characters inside a decoded value are never re-escaped.

Because `Rack::Utils.parse_query` in `query_string_valid?` decodes `%3D`/`%2C` before this reconstruction happens, an attacker can choose a single parameter whose decoded value embeds an `=`:
- Single-key structure: `{"a" => "1b=2"}` → serialized as `"a=1b=2"`.
- Two-key structure: `{"a" => "1", "b" => "2"}` → serialized as `"a=1"` + `"b=2"` = `"a=1b=2"`.

Both produce the identical `sorted_params` string, hence the identical HMAC. An attacker who obtains any legitimately Shopify-signed proxy request containing param `a=1b%3D2` (which Shopify's app proxy will happily sign for any query string an anonymous storefront visitor sends to the proxy path) can reuse that exact `signature` value on a forged request `?a=1&b=2&signature=<same>` sent directly to the app's proxy endpoint. `query_string_valid?` recomputes the same `sorted_params`/HMAC and returns `true`, even though Shopify never signed a two-key `{a: "1", b: "2"}` structure — only the single-key `{a: "1b=2"}` structure. The app's controller then operates on `params[:a] == "1"` and `params[:b] == "2"`, a parameter structure that diverges from what was cryptographically attested.

No other check in the concern (no timestamp expiry, no strict key/value re-validation) mitigates this; `verify_proxy_request` relies solely on `query_string_valid?`: [2](#0-1) 

### Impact Explanation
This is an accepted-forged-signed-request vulnerability (HMAC verification bypass on parameter boundaries) matching Shopify's "forged app proxy request" impact class. Concrete impact depends on what a given app's proxy controller does with `params`, but any app logic that trusts `params[:key]` as attested-by-Shopify data (e.g., branching on presence/absence of specific keys, or trusting a param that Shopify was supposed to control) can be manipulated by an attacker who can obtain one signed request and re-partition its parameters while reusing the signature.

### Likelihood Explanation
Exploitability requires the attacker to obtain one legitimately signed proxy request containing a parameter value with an embedded `=` (or comma) — which is directly attacker-influenceable since app proxy requests forward the anonymous shopper's own query string, appending `shop`/`path_prefix`/`timestamp`/`signature`. The attacker does not need Shopify's secret; they only need to observe the resulting `signature` in the URL that Shopify itself returns to their browser, then resubmit a differently-partitioned query string with that same signature directly to the app's public proxy route. This is fully reachable by an anonymous, unprivileged client and is deterministic/repeatable.

### Recommendation
Use an unambiguous, injective serialization for the signed string: escape `=`, `,`, and any join/sort delimiter within keys/values (or use a structured HMAC input, e.g., hashing a canonical JSON/array-of-pairs representation) instead of naive string concatenation, and insert an explicit separator between key=value pairs (in addition to escaping) so no combination of keys/values can collide with a different structure's serialization.

### Proof of Concept
```ruby
secret = "secret"
ShopifyApp.configure { |c| c.secret = secret }

controller = AppProxyVerificationController.new

# Structure 1: single key "a" whose value embeds "="
hash1 = { "a" => "1b=2" }
sig1  = controller.send(:calculated_signature, hash1)

# Structure 2: two distinct keys
hash2 = { "a" => "1", "b" => "2" }
sig2  = controller.send(:calculated_signature, hash2)

assert_equal sig1, sig2 # both serialize to "a=1b=2"

# Forged request reusing sig1 as the signature for hash2's structure
query_string = "a=1&b=2&signature=#{sig1}"
assert controller.send(:query_string_valid?, query_string) # passes verification
```
This demonstrates that `calculated_signature` produces identical output (and thus `query_string_valid?` accepts) for two logically different parameter sets, confirming the signature is not bound to the exact structured params Shopify actually signed.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L11-27)
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
