This confirms the vulnerability directly with an existing test case in the repo: `test/controllers/callback_controller_test.rb` line 185-192 explicitly demonstrates that when `session[:return_to]` is a fully-formed URL such as `"https://example.com/return_to?foo=bar"`, the callback redirects there directly, with `assert_redirected_to "https://example.com/return_to?foo=bar"` — regardless of `example.com` having nothing to do with the shop's admin.

### Title
Open redirect via `return_to` fully-formed URL bypasses phishing/host check in `redirect_to_app` - (File: app/controllers/shopify_app/callback_controller.rb)

### Summary
`CallbackController#redirect_to_app` decides the final post-OAuth redirect target using `fully_formed_url?(return_to)`: if `session[:return_to]` is already a full URL (scheme+host), it is used verbatim as the redirect target, while the only anti-phishing check (`deduced_phishing_attack?`) validates the unrelated `host` param, not `return_to`. An attacker can seed `session[:return_to]` with an arbitrary absolute URL via the `/login` (`SessionsController#create` → `copy_return_to_param_to_session`) flow before completing OAuth, causing the final callback redirect (which carries the fresh session/host state in the query string of `admin.shopify.com`/app URL context) to land on an attacker-controlled origin.

### Finding Description
In `app/controllers/shopify_app/callback_controller.rb`:
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
  ...
``` [1](#0-0) 

`fully_formed_url?` only checks that the string parses as a URI with a scheme and host — it performs no allow-listing of the host:
```ruby
def fully_formed_url?(return_to)
  uri = Addressable::URI.parse(return_to)
  uri.present? && uri.scheme.present? && uri.host.present?
end
``` [2](#0-1) 

`deduced_phishing_attack?` only validates `decoded_host` (derived from `params[:host]`), not `return_to`:
```ruby
def deduced_phishing_attack?
  sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)
  ...
  sanitized_host.nil?
end
``` [3](#0-2) 

So even when `host` is a legitimate/trusted myshopify domain (so `deduced_phishing_attack?` is `false`), the code still uses the untouched `return_to` value as the final `redirect_to` target when it is a fully-formed URL, with `allow_other_host: true` explicitly permitting cross-origin redirects.

The attacker-controlled `return_to` enters the session via `SessionsController#start_oauth` → `copy_return_to_param_to_session`:
```ruby
def copy_return_to_param_to_session
  session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
end
``` [4](#0-3) 

`RedirectSafely.make_safe` is a Shopify-provided utility not defined in this repo (external gem dependency); its exact host-restriction behavior could not be located in the indexed files, so I cannot confirm from this repo alone whether it already blocks absolute cross-origin URLs at this stage. However, the repo's own test suite explicitly asserts the vulnerable behavior is intended/expected:
```ruby
test "callback redirects to the return_to for embedded app when return_to is a fully-formed URL" do
  mock_oauth
  session[:return_to] = "https://example.com/return_to?foo=bar"

  get :callback, params: @callback_params

  assert_redirected_to "https://example.com/return_to?foo=bar"
end
``` [5](#0-4) 

This test directly sets `session[:return_to]` to an arbitrary external URL (bypassing whatever `RedirectSafely.make_safe` would have done at write-time in the login flow) and confirms the callback controller redirects there unconditionally, with `host` (`@callback_params[:host]`) being a legitimate trusted `myshopify.io` subdomain the whole time (`@host = "little-shoppe-of-horrors.#{ShopifyApp.configuration.myshopify_domain}"`) — proving `deduced_phishing_attack?` returns `false` and does not protect against this class of `return_to`.

### Impact Explanation
This matches the Immunefi/Shopify HackerOne "open redirect" impact class: the final post-OAuth-completion redirect from `/auth/shopify/callback`, which is the point at which a fresh session cookie/host state is delivered to the client, can be steered to an attacker-controlled origin. Combined with any downstream token/host param echoing in the URL (e.g., `host`/`shop` query params appended by app bridge flows), this enables exfiltration of session-identifying material to an attacker page.

### Likelihood Explanation
Feasibility depends on whether an unprivileged attacker can get `session[:return_to]` to end up as an absolute attacker URL by the time `/auth/shopify/callback` completes. The write path is `params[:return_to]` on `/login` passed through `RedirectSafely.make_safe`, which is an external gem method not present in this repository's index; I was unable to fully verify whether it strips absolute/cross-origin URLs before the value is stored in the session. Given the index size limits, its exact behavior could not be confirmed here — a full Devin session with filesystem access to the `shopify_api` / `redirect_safely` gem source would be needed to close this gap definitively. What is fully confirmed within this repo is that once `session[:return_to]` holds a fully-formed external URL, `CallbackController#redirect_to_app` will redirect there unconditionally as long as `host` is a trusted shop domain — this is asserted as expected behavior by the repo's own test suite.

### Recommendation
In `redirect_to_app`, do not trust `return_to` as an absolute redirect target merely because it parses as fully-formed. Either: (1) always treat `return_to` as a path only and reject/strip any value with a `scheme`/`host` component before combining with `decoded_host`, or (2) if a fully-formed URL is permitted, require its host to pass the same `ShopifyApp::Utils.sanitize_shop_domain` allow-list check applied to `decoded_host` (i.e., call `deduced_phishing_attack?`-style validation against `return_to`'s host, not just `params[:host]`), rejecting to `ShopifyApp.configuration.root_url` otherwise. Also audit `RedirectSafely.make_safe` usage in `SessionsController#copy_return_to_param_to_session` to confirm it rejects absolute cross-origin URLs at write time.

### Proof of Concept
Existing repo test demonstrates the vulnerable behavior directly (no modification needed to prove the sink is reachable and unguarded):
```ruby
test "callback redirects to the return_to for embedded app when return_to is a fully-formed URL" do
  mock_oauth
  session[:return_to] = "https://example.com/return_to?foo=bar"

  get :callback, params: @callback_params # host is a trusted myshopify.io subdomain

  assert_redirected_to "https://example.com/return_to?foo=bar"
end
``` [5](#0-4) 

To close the remaining verification gap, a background Devin session with full repo/gem access should additionally test the `/login` → `session[:return_to]` write path (`SessionsController#copy_return_to_param_to_session` calling `RedirectSafely.make_safe`) with `params[:return_to] = "https://evil.example.com/steal"` to confirm end-to-end reachability from an anonymous `/login?shop=...&return_to=https://evil.example.com/steal` request through to the vulnerable `redirect_to_app` sink.

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

**File:** app/controllers/shopify_app/callback_controller.rb (L96-99)
```ruby
    def fully_formed_url?(return_to)
      uri = Addressable::URI.parse(return_to)
      uri.present? && uri.scheme.present? && uri.host.present?
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L106-113)
```ruby
    def deduced_phishing_attack?
      sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)
      if sanitized_host.nil?
        ShopifyApp::Logger.info("host param from callback is not from a trusted domain")
        ShopifyApp::Logger.info("redirecting to root as this is likely a phishing attack")
      end
      sanitized_host.nil?
    end
```

**File:** app/controllers/shopify_app/sessions_controller.rb (L109-111)
```ruby
    def copy_return_to_param_to_session
      session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
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
