Found it: `SessionsController#copy_return_to_param_to_session` sets `session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/")` directly from the attacker-supplied `return_to` query param at the start of OAuth (`/login?return_to=...`), before any host validation runs.

### Title
`session[:return_to]` set from unsanitized `RedirectSafely.make_safe` result can bypass phishing check and cause open redirect after OAuth - ([File: app/controllers/shopify_app/sessions_controller.rb], cross-reference [File: app/controllers/shopify_app/callback_controller.rb])

### Summary
`SessionsController#copy_return_to_param_to_session` stores `RedirectSafely.make_safe(params[:return_to], "/")` into `session[:return_to]` at OAuth start. `RedirectSafely.make_safe` (Rails' `redirect_safely` gem) only blocks scheme-relative URLs (`//host`) and non-http(s) schemes — it does not restrict the URL to the app's own host. An attacker-controlled absolute URL like `https://evil.example/x` therefore survives `make_safe` unchanged and is stored in `session[:return_to]`.

### Finding Description
Flow: `GET /login?shop=<attacker's own shop>&return_to=https://evil.example/x` → `SessionsController#authenticate` → `#start_oauth` → `copy_return_to_param_to_session` sets `session[:return_to] = RedirectSafely.make_safe("https://evil.example/x", "/")`, which returns the URL as-is since it has an `http`/`https` scheme (only `//`-prefixed scheme-relative URLs and non-whitelisted schemes are rejected by `make_safe`) [1](#0-0) .

After OAuth completes, `CallbackController#redirect_to_app` reads `return_to = session.delete(:return_to)` and checks `fully_formed_url?(return_to)`, which parses the string with `Addressable::URI` and returns true if it has both a scheme and a host — true for `https://evil.example/x` [2](#0-1) . Because it's "fully formed," `redirect_to_app` uses `return_to` directly as the redirect target instead of prefixing it with `decoded_host`.

Critically, `deduced_phishing_attack?` only validates `decoded_host` (derived from `params[:host]`, sanitized against `ShopifyApp::Utils.sanitize_shop_domain`) — it never inspects `return_to` itself [3](#0-2) . Since the attacker controls their own shop's `host` param legitimately (it will be a valid myshopify host for their own shop), `deduced_phishing_attack?` returns `false`, and the redirect proceeds to `redirect_to(return_to, allow_other_host: true)` — landing the browser on `https://evil.example/x` [4](#0-3) .

Existing checks fail to stop this because: (1) `RedirectSafely.make_safe` validates only URL syntax/scheme, not host allowlisting; (2) `fully_formed_url?` is a syntactic check with no host validation; (3) `deduced_phishing_attack?` validates the OAuth `host` param, not the unrelated `return_to` value; (4) `allow_other_host: true` explicitly disables Rails' own open-redirect protection for this call.

### Impact Explanation
This is a classic OAuth-flow open redirect: after a legitimate merchant completes install/login on their own shop, the final redirect lands on an attacker-controlled domain. Depending on what is appended to the query string during the flow (e.g., any token/session artifacts, or simply the ability to serve a convincing phishing page right after real Shopify authentication), this enables phishing and session/context exfiltration to a third-party domain. This matches Shopify's "Open Redirect" impact class, elevated by post-auth trust context (redirect happens immediately after real OAuth success, increasing user trust).

### Likelihood Explanation
Fully attacker-reachable with no privileges beyond initiating their own OAuth flow: they need only append `return_to=https://evil.example/...` to their own `/login` (or install) request for a shop they control (or any shop, since `shop` need not be privileged), then complete OAuth normally. The path is deterministic and repeatable and requires no victim interaction beyond following the link the attacker sends them (typical phishing setup: send merchant a link to `/login?shop=X&return_to=https://evil.example`).

### Recommendation
In `CallbackController#redirect_to_app` (and/or `SessionsController#copy_return_to_param_to_session`), validate that `return_to`, when fully-formed, has a host equal to `decoded_host`'s host (or is same-origin with the app / `ShopifyApp.configuration.root_url`); otherwise discard it and fall back to `ShopifyApp.configuration.root_url`, exactly as already done for `deduced_phishing_attack?`. Do not accept externally-hosted absolute URLs for `return_to` at all — restrict `RedirectSafely.make_safe`/subsequent checks to relative paths, or explicitly allow-list the app's own host(s).

### Proof of Concept
```ruby
test "redirect_to_app open-redirects to attacker host via return_to fully-formed URL" do
  # Step 1: simulate SessionsController storing attacker-controlled return_to
  get login_path(shop: "shop1.myshopify.com", return_to: "https://evil.example/x")
  # session[:return_to] now == "https://evil.example/x" (RedirectSafely.make_safe allows absolute http(s) URLs)

  # Step 2: complete OAuth callback normally for the attacker's own shop
  get callback_path(shop: "shop1.myshopify.com", host: valid_host_param_for("shop1.myshopify.com"), code: "...", hmac: "...", timestamp: Time.now.to_i)

  assert_redirected_to "https://evil.example/x"
  # Expected (fixed) behavior: assert_redirected_to ShopifyApp.configuration.root_url
end
```
This demonstrates `fully_formed_url?` accepting the attacker URL and `deduced_phishing_attack?` not catching it because it only checks `decoded_host`, not `return_to`.

### Citations

**File:** app/controllers/shopify_app/sessions_controller.rb (L109-111)
```ruby
    def copy_return_to_param_to_session
      session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L80-99)
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
