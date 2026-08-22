### Title
`calculated_signature` join-without-delimiter allows key/value boundary collision, enabling forged app-proxy parameter injection - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Summary
`ShopifyApp::AppProxyVerification#calculated_signature` builds the string to be HMAC-signed by formatting each param as `"key=value"` and concatenating the sorted list with `.join` (no delimiter). Because there is no separator between successive `key=value` pairs, two structurally different query hashes can serialize to the exact same signing string, causing the same HMAC signature to validate two different parameter sets.

### Finding Description
In `query_string_valid?`, the request's query string is parsed with `Rack::Utils.parse_query`, the `signature` is stripped out, and the remaining hash is passed to `calculated_signature`: [1](#0-0) 

`calculated_signature` formats each `key => value` pair as `"#{k}=#{Array(v).join(",")}"`, sorts the resulting strings, and joins them with no separator before HMAC-signing: [2](#0-1) 

Because `"="` characters inside a value are not escaped/delimited from the following `key=value` pair, a single param whose value contains a literal `=` can produce an identical canonical string to two separate params. For example:
- Hash A = `{"a" => "1b=2"}` → formatted string `"a=1b=2"`
- Hash B = `{"a" => "1", "b" => "2"}` → formatted/sorted strings `"a=1"`, `"b=2"` → joined `"a=1b=2"`

Both hashes produce the identical signing string `"a=1b=2"`, so `calculated_signature(A) == calculated_signature(B)` for any secret.

Exploit path: any anonymous visitor to the merchant's storefront can request the app-proxy path with a crafted query string containing a `=`-embedded value (e.g. `?a=1%3D2`). Shopify's real proxy will compute and attach a valid signature over hash A `{"a"=>"1b=2", shop, path_prefix, timestamp}`. The attacker then sends a request directly to the app's public endpoint (bypassing Shopify) with the borrowed signature but a different, colliding query hash B (e.g. `a=1&b=2&shop=...&path_prefix=...&timestamp=...&signature=<borrowed>`), which decomposes into two distinct parameters instead of one. Since `calculated_signature(B)` equals the borrowed signature, `ActiveSupport::SecurityUtils.secure_compare` in `query_string_valid?` succeeds and the forged, differently-structured request is accepted as authentic — without ever knowing `ShopifyApp.configuration.secret`.

None of the existing checks stop this: `secure_compare` only guards against timing attacks on the comparison, and there is no canonicalization step (e.g. delimiter, length-prefixing, or escaping of `=`/parameter boundaries) that would prevent two distinct hashes from serializing identically.

### Impact Explanation
This enables acceptance of a forged/relabeled app-proxy request: an attacker who only observes or triggers one legitimately-signed proxy request (via a normal, unprivileged storefront visit) can smuggle extra or restructured parameters into a request the app trusts as Shopify-signed, without possessing the app's shared secret. This matches the "forged app-proxy request" impact class called out for this component — it undermines the integrity guarantee that `verify_proxy_request` is supposed to provide (that only Shopify, holder of the secret, can produce parameter sets that pass verification).

### Likelihood Explanation
Preconditions are minimal and require no privileges: the attacker only needs to (1) cause a genuine app-proxy request to be signed with a value containing a URL-decoded `=` character (trivially done by visiting the storefront proxy URL with an encoded `%3D` in a query value), and (2) replay that signature against the app directly with a colliding, differently-partitioned query hash. This is fully repeatable and deterministic (HMAC of an identical string always produces the identical signature), with no timing or race dependency.

### Recommendation
Change `calculated_signature` to build an unambiguous canonical string, e.g. join formatted pairs with a delimiter that cannot appear in keys (or escape `=`/delimiter characters within keys and values before joining), such as:
```ruby
sorted_params = query_hash_without_signature.sort.map { |k, v| "#{k}=#{Array(v).join(",")}" }.join("&")
```
using a fixed separator like `"&"` between pairs prevents different (key, value) partitions from collapsing into the same signing string. (Note: Shopify's own documented app-proxy signature algorithm intentionally omits a delimiter between pairs, so any fix here would diverge from Shopify's server-side computation and must be verified against Shopify's actual signing implementation before shipping.)

### Proof of Concept
```ruby
test "calculated_signature collides across differently-partitioned param sets" do
  controller = AppProxyVerificationController.new

  hash_a = { "a" => "1b=2" }
  hash_b = { "a" => "1", "b" => "2" }

  sig_a = controller.send(:calculated_signature, hash_a)
  sig_b = controller.send(:calculated_signature, hash_b)

  assert_equal sig_a, sig_b # two distinct parameter sets produce an identical signature
end
```
This demonstrates that `calculated_signature({"a" => "1b=2"})` and `calculated_signature({"a" => "1", "b" => "2"})` are identical for any secret, confirming the key/value boundary confusion in `lib/shopify_app/controller_concerns/app_proxy_verification.rb`.

### Citations

**File:** lib/shopify_app/controller_concerns/app_proxy_verification.rb (L17-27)
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
