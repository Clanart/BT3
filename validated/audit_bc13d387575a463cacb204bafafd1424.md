### Title
Open redirect in `redirect_to_app` via fully-formed `return_to` bypassing host/phishing check - ([File: app/controllers/shopify_app/callback_controller.rb])

### Summary
`CallbackController#redirect_to_app` builds the post-OAuth redirect target using `session[:return_to]` when embedded. If `return_to` is a "fully-formed URL" (has scheme + host), that value is used *directly* as the redirect target, and the subsequent `deduced_phishing_attack?` check only validates `params[:host]` — never the actual `redirect_to` value chosen — so a fully-formed, attacker-influenced `return_to` is honored even though the `host` param is fully valid/trusted.

### Finding Description
`redirect_to_app` computes:
```ruby
redirect_to = if fully_formed_url?(return_to)
  return_to
else
  "#{decoded_host}#{return_to}"
end
redirect_to = ShopifyApp.configuration.root_url if deduced_phishing_attack?
redirect_to(redirect_to, allow_other_host: true)
``` [1](#0-0) 

`deduced_phishing_attack?` only inspects `decoded_host` (derived from `params[:host]`), never the `return_to`/`redirect_to` value itself: [2](#0-1) 

This means: if an attacker supplies a trusted/valid `host` param (e.g., their own shop's myshopify host, which is normal and expected in a real OAuth flow) but a fully-formed `return_to` pointing to an external domain, `deduced_phishing_attack?` returns `false` (host is legitimate), so the redirect proceeds unmodified to the attacker-controlled `return_to` URL — confirmed directly by the existing test: [3](#0-2) 
which asserts the app redirects to `https://example.com/return_to?foo=bar` when `session[:return_to]` is set to that value.

The normal entry point for `return_to` is `SessionsController#copy_return_to_param_to_session`, which runs `RedirectSafely.make_safe(params[:return_to], "/")` before OAuth begins: [4](#0-3) 
`RedirectSafely` (external gem) is generally expected to strip absolute/external URLs down to a safe default, which is the primary defense intended to prevent `return_to` from ever becoming a "fully formed URL" pointing off-domain. However, this repo's own test suite explicitly sets `session[:return_to] = "https://example.com/return_to?foo=bar"` directly and expects the callback to honor it verbatim — demonstrating that `redirect_to_app` itself performs **no validation of the destination host of `return_to`** and relies entirely on `RedirectSafely` at a separate, earlier code path (login) to have already sanitized it. If `RedirectSafely.make_safe` has any edge-case bypass (e.g., protocol-relative `//`, unusual encodings, or if session `return_to` is set through any other path not going through `copy_return_to_param_to_session`), `redirect_to_app` provides no secondary defense — the phishing check is host-scoped only, not return_to-scoped.

Regarding the "nil/blank fails closed" invariant specifically requested: if `params[:host]` is nil/blank, `decoded_host` calls `ShopifyAPI::Auth.embedded_app_url(nil)` (external `shopify_api` gem method, not in this repo, so its exact nil-handling behavior cannot be verified from this codebase). If that call raises or returns something whose `URI(...).host` is `nil`, `ShopifyApp::Utils.sanitize_shop_domain(nil)` returns `nil` via `uri_from_shop_domain` guard `return if uri.nil? || uri.host.nil?`, and `deduced_phishing_attack?` returns `true`, correctly failing closed to `ShopifyApp.configuration.root_url`: [5](#0-4) 
This part of the invariant (host-based phishing detection) does fail closed. The vulnerability is orthogonal: it's that a fully-formed `return_to` bypasses this host check entirely rather than being subject to it.

### Impact Explanation
If reachable, this results in the callback redirecting to an attacker-controlled origin immediately after OAuth completion, at a point where a freshly-set session cookie (`update_rails_cookie`) and `host`/shop context exist in the browser. This matches the "open redirect delivering session/host to attacker" impact class. However, the primary safeguard (`RedirectSafely.make_safe` in `SessionsController#copy_return_to_param_to_session`) is an external gem not present in this repository, so I cannot confirm from this codebase alone whether an unprivileged attacker can actually get `session[:return_to]` set to a fully-formed external URL through the public `/login` (`SessionsController#create`) entrypoint. The `callback` action itself does not accept a `return_to` param — it only reads from `session[:return_to]`, which is written earlier in the flow behind `RedirectSafely.make_safe`.

### Likelihood Explanation
Exploitability hinges entirely on whether `RedirectSafely.make_safe(params[:return_to], "/")` can be made to return a fully-formed absolute URL (scheme+host) for some attacker-supplied `return_to` value in the `/login` request. This library's implementation is not part of this repository's index, so it cannot be verified here. Within `callback_controller.rb` alone, there is no independent re-validation of `return_to`'s destination host once it is deemed "fully formed" — the only check (`deduced_phishing_attack?`) validates `host`, not `return_to`.

### Recommendation
In `redirect_to_app`, when `fully_formed_url?(return_to)` is true, validate that `return_to`'s host is itself a trusted Shopify/admin domain (e.g., via `ShopifyApp::Utils.sanitize_shop_domain(URI(return_to).host)`), not just gate on `params[:host]`. Alternatively, never treat a session-stored `return_to` as a fully-formed absolute URL destination at all — restrict it to a relative path, consistent with how `RedirectSafely.make_safe(params[:return_to], "/")` is intended to be used at the point it's first stored.

### Proof of Concept
Existing test (already in repo) demonstrates the exact behavior:
```ruby
test "callback redirects to the return_to for embedded app when return_to is a fully-formed URL" do
  mock_oauth
  session[:return_to] = "https://example.com/return_to?foo=bar"

  get :callback, params: @callback_params # host is a valid, trusted myshopify host

  assert_redirected_to "https://example.com/return_to?foo=bar"
end
``` [3](#0-2) 
This confirms `redirect_to_app` will redirect to an arbitrary external origin whenever `session[:return_to]` holds a fully-formed URL, regardless of the (valid) `host` param — the `deduced_phishing_attack?` check does not cover this case. Full confirmation of unprivileged reachability requires verifying the `RedirectSafely` gem's `make_safe` behavior against crafted `return_to` values (e.g., `//evil.com`, `https:/evil.com`, `\evil.com`) via `GET /login?shop=...&return_to=<payload>`, which is outside this repository's indexed code.

### Citations

**File:** app/controllers/shopify_app/callback_controller.rb (L80-94)
```ruby
    def redirect_to_app
      if ShopifyAPI::Context.embedded?
        return_to = session.delete(:return_to)
        redirect_to = if fully_formed_url?(return_to)
          return_to
        else
          "#{decoded_host}#{return_to}"
        end

        redirect_to = ShopifyApp.configuration.root_url if deduced_phishing_attack?
        redirect_to(redirect_to, allow_other_host: true)
      else
        redirect_to(return_address)
      end
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L101-113)
```ruby
    def decoded_host
      @decoded_host ||= ShopifyAPI::Auth.embedded_app_url(params[:host])
    end

    # host param doesn't match the configured myshopify_domain
    def deduced_phishing_attack?
      sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)
      if sanitized_host.nil?
        ShopifyApp::Logger.info("host param from callback is not from a trusted domain")
        ShopifyApp::Logger.info("redirecting to root as this is likely a phishing attack")
      end
      sanitized_host.nil?
    end
```

**File:** test/controllers/callback_controller_test.rb (L185-192)
```ruby
    test "callback redirects to the return_to for embedded app when return_to is a fully-formed URL" do
      mock_oauth
      session[:return_to] = "https://example.com/return_to?foo=bar"

      get :callback, params: @callback_params # host is required for App Bridge 2.0

      assert_redirected_to "https://example.com/return_to?foo=bar"
    end
```

**File:** app/controllers/shopify_app/sessions_controller.rb (L109-111)
```ruby
    def copy_return_to_param_to_session
      session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
    end
```

**File:** lib/shopify_app/utils.rb (L14-27)
```ruby
      def sanitize_shop_domain(shop_domain)
        uri = uri_from_shop_domain(shop_domain)
        return if uri.nil? || uri.host.nil?

        trusted_domains.each do |trusted_domain|
          no_shop_name_in_subdomain = uri.host == trusted_domain
          from_trusted_domain = trusted_domain == uri.domain

          return myshopify_domain_from_unified_admin(uri) if unified_admin?(uri) && from_trusted_domain
          return nil if no_shop_name_in_subdomain || uri.host&.empty?
          return uri.host if from_trusted_domain
        end
        nil
      end
```
