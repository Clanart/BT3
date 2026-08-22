### Title
Open redirect via session-seeded `return_to` fully-formed URL bypassing host-domain phishing check - (File: app/controllers/shopify_app/callback_controller.rb)

### Summary
In `redirect_to_app`, when `return_to` (session value) parses as a fully-formed URL (`fully_formed_url?`), it is used verbatim as the post-OAuth redirect target, while `deduced_phishing_attack?` only re-validates the separate `host` param/`decoded_host`, never the actual computed `redirect_to` value. This means a legitimate `host` param combined with an external `return_to` URL results in a redirect straight to an attacker-controlled origin after OAuth completes.

### Finding Description
`redirect_to_app` computes:
```ruby
return_to = session.delete(:return_to)
redirect_to = if fully_formed_url?(return_to)
  return_to
else
  "#{decoded_host}#{return_to}"
end
redirect_to = ShopifyApp.configuration.root_url if deduced_phishing_attack?
redirect_to(redirect_to, allow_other_host: true)
``` [1](#0-0) 

`fully_formed_url?` only checks that the string has a scheme and host — it accepts any absolute URL, including `https://evil.com`: [2](#0-1) 

`deduced_phishing_attack?` validates `decoded_host` (derived from `params[:host]`), not the value that will actually be redirected to: [3](#0-2) 

So when `return_to` is a fully-formed external URL, the phishing check is entirely decoupled from the redirect target — it validates `host`, then unconditionally uses `return_to` as-is with `allow_other_host: true`. This exact behavior is confirmed and asserted as expected in the test suite: session[:return_to] = "https://example.com/return_to?foo=bar" results in `assert_redirected_to "https://example.com/return_to?foo=bar"` [4](#0-3) , contrasted with the phishing-detection test that only manipulates `host`, not `return_to` [5](#0-4) .

However, I could not fully verify within this repo whether `session[:return_to]` can actually be attacker-seeded with an arbitrary external absolute URL through the normal login path, because the value is filtered through `RedirectSafely.make_safe` before being stored:
```ruby
def copy_return_to_param_to_session
  session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
end
``` [6](#0-5) 

The `redirect_safely` gem's `make_safe` implementation is not vendored in this repo (only referenced in `Gemfile.lock`/`shopify_app.gemspec`) [7](#0-6) , so I cannot confirm from this codebase alone whether it strips/rejects absolute external URLs (typical behavior of that gem is to only allow same-host or relative paths) or whether it can be bypassed (e.g., via `//evil.com`, backslash tricks, or a URL parsing discrepancy between `RedirectSafely` and `Addressable::URI` used in `fully_formed_url?`). The other path that sets `session[:return_to]` — `redirect_to_login` — always constructs it from `request.path`/referer path, which is not attacker-suppliable as an absolute external URL [8](#0-7) .

### Impact Explanation
If `RedirectSafely.make_safe` can be bypassed or does not block absolute external URLs (unverified in this repo), the impact is a classic open redirect at the OAuth completion endpoint: the freshly minted session cookie is already set on the browser via `update_rails_cookie` before this redirect fires, and app_bridge / host-encoded post-login navigation would carry the merchant into an attacker page, enabling phishing/token-exfiltration flows (e.g., fake re-auth prompts capturing the session, or forwarding of `host`/`shop` query data appended by the app to the attacker's URL). This matches the "open redirect delivering session/host to attacker" impact class.

### Likelihood Explanation
Exploitability is entirely contingent on whether `RedirectSafely.make_safe(params[:return_to], "/")` in `sessions_controller.rb` permits (or can be tricked into permitting) an absolute external URL to persist into `session[:return_to]`. Without being able to inspect that gem's source in this index, I cannot confirm this precondition is actually reachable by an unprivileged attacker via the public `/login` (or equivalent) endpoint alone. The `callback_controller.rb` logic itself, in isolation, has no defense against a fully-formed malicious `return_to` reaching `redirect_to_app` — the only gate is upstream input sanitization at the point `return_to` is first stored in session.

### Recommendation
- In `redirect_to_app`, validate the final `redirect_to` value's host (not just `decoded_host`) against the trusted-domain check before redirecting, e.g., re-run `deduced_phishing_attack?`-style validation against `URI(redirect_to).host` whenever `fully_formed_url?(return_to)` is true, rather than only checking `decoded_host`.
- Alternatively, drop `allow_other_host: true` combined with attacker-influenced `return_to`, and instead only allow fully-formed URLs whose host matches the shop's own trusted domain (`ShopifyApp::Utils.sanitize_shop_domain`).
- Independently confirm (in a Devin session with full gem access) whether `RedirectSafely.make_safe` actually blocks absolute URLs; if it does, this finding is mitigated at the input layer and only matters if that gem is bypassed or removed.

### Proof of Concept
```ruby
test "callback redirect_to_app is not re-validated for fully-formed return_to host" do
  mock_oauth
  session[:return_to] = "https://evil.example.com/steal?token=1"

  get :callback, params: @callback_params # host param is a legitimate myshopify host

  # Current behavior: redirect goes straight to the attacker-controlled URL,
  # deduced_phishing_attack? never inspects redirect_to, only decoded_host.
  assert_redirected_to "https://evil.example.com/steal?token=1"
end
```
This mirrors the existing test at [4](#0-3)  — confirming the gem's own test suite already documents this exact redirect behavior as "working as intended," which is the root of the concern: the phishing check in `deduced_phishing_attack?` never inspects the actual `redirect_to` target when `return_to` is fully-formed.

**Caveat:** Full confirmation of exploitability requires verifying `RedirectSafely.make_safe`'s behavior (not available in this index) — a Devin session with full filesystem/dependency access should inspect the `redirect_safely` gem source to determine if/how an unprivileged attacker can smuggle an absolute external URL into `params[:return_to]` at `/login`.

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

**File:** test/controllers/callback_controller_test.rb (L125-138)
```ruby
    test "#callback returns to root if the host in the param doesn't match configuration indicating a potential phishing attack" do
      host = "hackerman-evil-site.com/hide-yo-wife-hide-yo-kids"
      encoded_host = Base64.strict_encode64(host + "/admin")
      hacker_params = @callback_params.dup
      hacker_params[:host] = encoded_host
      ShopifyAPI::Auth::Oauth::AuthQuery.stubs(:new).with(**hacker_params).returns(@auth_query)
      ShopifyAPI::Auth::Oauth.expects(:validate_auth_callback).returns({
        cookie: @stubbed_cookie,
        session: @stubbed_session,
      })

      get :callback, params: hacker_params
      assert_redirected_to ShopifyApp.configuration.root_url
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

**File:** shopify_app.gemspec (L1-1)
```text
# frozen_string_literal: true
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L104-123)
```ruby
    def redirect_to_login
      if requested_by_javascript?
        add_top_level_redirection_headers(ignore_response_code: true)
        ShopifyApp::Logger.debug("Login redirect request is a XHR")
        head(:unauthorized)
      else
        if request.get?
          path = request.path
          query = sanitized_params.to_query
        else
          referer = URI(request.referer || "/")
          path = referer.path
          query = Rack::Utils.parse_nested_query(referer.query)
          query = query.merge(sanitized_params).to_query
        end
        session[:return_to] = return_to_url(path, query)
        ShopifyApp::Logger.debug("Redirecting to #{login_url_with_optional_shop}")
        redirect_to(login_url_with_optional_shop)
      end
    end
```
