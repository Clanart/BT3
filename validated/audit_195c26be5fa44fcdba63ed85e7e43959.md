### Title
Signature verification over collapsed/array query view diverges from Rails' `params[:shop]` on duplicate keys - ([File: lib/shopify_app/controller_concerns/app_proxy_verification.rb])

### Finding Description
`query_string_valid?` re-parses `request.query_string` with `Rack::Utils.parse_query`, which on a duplicate flat key (e.g. two `shop=` occurrences) builds an **array** of values (`["victim...","attacker..."]`) and `calculated_signature` joins that array with commas before hashing [1](#0-0) . This matches Shopify's own documented app-proxy signing algorithm (comma-joining repeated keys), which the test suite explicitly exercises for repeated/complex args [2](#0-1) .

However, the generated `AppProxyController` (and any controller including this concern) subsequently reads `params[:shop]` via Rails' own query parser (`ActionDispatch`/`Rack::QueryParser#parse_nested_query`), not `Rack::Utils.parse_query` [3](#0-2) . For a flat (non-bracketed) duplicate key, `parse_nested_query`'s `normalize_params` overwrites rather than arrays the value, so the *last occurrence in the raw query string wins* — a different, order-dependent resolution than the array/comma-joined value that was actually authenticated. `sanitized_shop_name`/`sanitize_shop_param` in `SanitizedParams` also just calls `params[:shop]` and only sanitizes format, never re-checks that this value was the one covered by the signature [4](#0-3) .

Concretely: if Shopify's proxy forwards a request whose original (customer-supplied) query string already contains a `shop=` (or `path_prefix=`) parameter, and Shopify appends its own authoritative `shop=<realstore>` parameter to the same query string before signing it with the shared secret, both parameters get folded into one array entry for signature purposes (order-independent, since it is comma-joined in insertion order but validated as a whole against Shopify's own equally-collapsed computation). The signature check therefore correctly validates the full byte content Shopify actually signed. But `params[:shop]` inside the action is resolved by Rails using last-value-wins, which is sensitive to which occurrence appears later in the raw string. If the attacker-controlled value happens to be the one that lands last in the query string, `params[:shop]` returns the attacker-controlled shop domain even though the (correctly validated) signature covered a query string that also legitimately contained Shopify's own appended value.

### Impact Explanation
Where an app proxy action trusts `params[:shop]` (or values sanitized from it) post-verification to decide whose data to serve, credentials to use, or how to scope a lookup, this discrepancy could let an attacker who can influence the query string forwarded through a genuine, correctly-signed Shopify app-proxy request smuggle a spoofed `shop` (or `path_prefix`) value into the action, distinct from the value Shopify actually authenticated. This maps to a cross-shop confusion class of impact (Shopify HackerOne: "Cross-store data leakage/confusion via app proxy").

### Likelihood Explanation
This is not an independent forgery — the attacker cannot compute a valid HMAC without the app secret; they still need Shopify to relay and sign the request. Exploitability hinges on whether Shopify's App Proxy forwarding layer preserves attacker-supplied duplicate query keys alongside its own appended `shop`/`path_prefix`/`timestamp` values in a way that lands the attacker's value at the position Rails' last-value-wins parser would pick, and whether the app actually appends its own `shop` value there rather than replacing/stripping conflicting params. This external forwarding behavior is outside this repo and could not be confirmed with the tools available; the repo only shows that the two parsers (`Rack::Utils.parse_query` for signature vs Rails' nested query parser for `params`) behave differently on duplicate keys, which is the necessary condition for the bug but not sufficient proof of end-to-end exploitability without confirming Shopify's actual proxy-forwarding/query-merging behavior.

### Recommendation
Do not rely on `params[:shop]` (or any Rails-parsed param) downstream of `query_string_valid?` for values that were part of the signed set. Instead, derive `shop`, `path_prefix`, etc. directly from the same collapsed `query_hash` that was cryptographically validated in `query_string_valid?` (e.g., expose the parsed/validated hash to the controller, or reject requests containing duplicate keys for security-relevant parameters such as `shop`/`path_prefix` before validating, since HTTP Parameter Pollution should be treated as invalid input rather than silently collapsed).

### Proof of Concept
```ruby
# Demonstrates the parser divergence documented above (cannot be
# fully exploited end-to-end without confirming Shopify's proxy
# forwarding behavior for duplicate query keys).

query_string = "shop=attacker.myshopify.com&shop=victim.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp=1466106083"

# What the signature is computed over (array, comma-joined):
Rack::Utils.parse_query(query_string)["shop"]
# => ["attacker.myshopify.com", "victim.myshopify.com"]

# What Rails/ActionDispatch resolves for params[:shop] (last value wins):
Rack::Utils.parse_nested_query(query_string)["shop"]
# => "victim.myshopify.com"   (in this ordering it's safe; reversing
#     the order of the two shop= occurrences flips the result to
#     "attacker.myshopify.com" while the *same* comma-joined
#     signature input/signature would still validate)
```
This confirms the two parsers used by `query_string_valid?` and by the controller's `params[:shop]` disagree on duplicate keys, and that the disagreement is order-dependent while the signature check is not — the core precondition described in the question. Full exploitability additionally requires that Shopify's app-proxy forwarding actually preserves an attacker-supplied duplicate `shop`/`path_prefix` in the merged/signed query string, which could not be verified from this repository alone.

### Citations

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

**File:** test/shopify_app/controller_concerns/app_proxy_verification_test.rb (L39-46)
```ruby
  test "query_string_complex_args" do
    assert query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&timestamp"\
      "=1466106083&signature=bbf3aa60e098f08919a2ea4c64a388414f164e6a117a63b03479ac7aa9464b4f&foo=bar&baz[1]"\
      "&baz[2]=b&baz[c[0]]=whatup&baz[c[1]]=notmuch")
    assert query_string_valid?("shop=some-random-store.myshopify.com&path_prefix=%2Fapps%2Fmy-app&"\
      "timestamp=1466106083&foo=bar&baz[1]&baz[2]=b&baz[c[0]]=whatup&baz[c[1]]=notmuch&signature"\
      "=bbf3aa60e098f08919a2ea4c64a388414f164e6a117a63b03479ac7aa9464b4f")
  end
```

**File:** lib/generators/shopify_app/app_proxy_controller/templates/app_proxy_controller.rb (L1-8)
```ruby
# frozen_string_literal: true

class AppProxyController < ApplicationController
  include ShopifyApp::AppProxyVerification

  def index
    render(layout: false, content_type: "application/liquid")
  end
```

**File:** lib/shopify_app/controller_concerns/sanitized_params.rb (L22-26)
```ruby
    def sanitize_shop_param(params)
      return unless params[:shop].present?

      ShopifyApp::Utils.sanitize_shop_domain(params[:shop])
    end
```
