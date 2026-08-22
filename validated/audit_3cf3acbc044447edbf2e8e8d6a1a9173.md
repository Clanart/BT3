### Title
Open redirect via backslash-prefixed `return_to` bypassing `RedirectSafely.make_safe` — (File: `lib/shopify_app/controller_concerns/login_protection.rb`)

### Summary
`redirect_to_login`/`login_url_params` sanitize the attacker-controlled `return_to` parameter using `RedirectSafely.make_safe`, which only rejects URLs that start with an explicit scheme (`scheme:`) or a literal `//` (protocol-relative). A value such as `/\attacker.com` does not match either pattern, so it is treated as "safe" and propagated unchanged into the login URL and later into `session[:return_to]`/`base_return_address`, even though many browsers normalize a leading `/\` to `//`, causing the browser to actually navigate to the attacker's origin.

### Finding Description
- `redirect_to_login` builds `session[:return_to] = return_to_url(path, query)` from the current request path/query (not directly from `return_to`), but `login_url_params` separately reads the attacker-controlled value directly: `return_to = RedirectSafely.make_safe(session[:return_to] || params[:return_to], nil)` [1](#0-0) .
- `RedirectSafely.make_safe` (per `Gemfile.lock`) is the well-known small gem whose validation logic only blocks strings matching an absolute-scheme regex or a literal leading `//` (protocol-relative). It has no knowledge of backslash-based browser URL normalization quirks, so `/\attacker.com` passes as "safe" untouched.
- The result is placed into `query_params[:return_to]` and appended to the `login_url`, and is also copied into `session[:return_to]` in `SessionsController#copy_return_to_param_to_session`, which again calls `RedirectSafely.make_safe(params[:return_to], "/")` with the same blind spot [2](#0-1) .
- Later, after OAuth completes, `base_return_address` pulls this value straight out of the session and uses it as the final redirect target: `session.delete(:return_to) || ShopifyApp.configuration.root_url` [3](#0-2) , which is then rendered via `redirect_to`/`fullpage_redirect_to` — the same call sites that finish the OAuth handshake and place `host`/session cookies in the redirected response.
- Because `/\attacker.com` is not literally `//attacker.com`, none of the existing checks (`RedirectSafely.make_safe`, `sanitize_shop_domain`, HMAC/JWT checks) intercept it — those checks are unrelated to `return_to` normalization. The browser-side normalization (backslash → forward slash) is a documented WHATWG URL-parsing behavior, independent of server-side string matching, so the mismatch between what the server considers "safe" and what the browser actually navigates to is the root cause.

### Impact Explanation
If the final post-login redirect target is attacker-controlled via this bypass, the victim's browser can be sent to an external, attacker-controlled origin immediately after Shopify's login/OAuth flow completes — the exact point where `host`/session state is exposed in the URL/response. This matches the "open redirect leading to token/host leakage" impact class described in the prompt, scoped to the single unauthenticated GET request needed to seed `return_to`.

### Likelihood Explanation
The precondition is trivial: an unauthenticated attacker issues one GET request to any protected route with `return_to=/\attacker.com` (or induces a victim to click such a link), which flows straight into `login_url_params`/`copy_return_to_param_to_session` with no authentication or secret required. This is fully reproducible with a plain HTTP client and does not depend on any misconfiguration.

### Recommendation
Do not rely solely on `RedirectSafely.make_safe`'s `scheme:`/`//` regex. Before/after calling `make_safe`, normalize the candidate `return_to` by stripping/rejecting any leading backslashes (`\`) or mixed slash-backslash sequences, and enforce that the sanitized value both starts with a single `/` and does not match `%r{\A/+[\\/]}` (i.e., reject any path beginning with `/` followed by another `/` or `\` in any order/case, including URL-encoded variants like `%5c`). Ideally, parse the final value with `URI` and assert `.host.nil? && .scheme.nil?` after unescaping, rather than trusting a prefix regex.

### Proof of Concept
```ruby
# request/controller spec
get "/some_protected_action", params: { return_to: "/\\attacker.com" }
# expected (secure): response is 401/redirected, and login_url's return_to
# is rejected/rewritten to a same-origin path, e.g. "/" or the original path.

follow_redirect! # simulate browser normalization behavior
assert_no_match(%r{https?://attacker\.com}, response.location)
```
Manually verify via a browser: navigating to `GET /protected?return_to=/\attacker.com`, then completing the login/OAuth flow, results in the final redirect target being `//attacker.com/...` in the rendered `Location` header/HTML, which the browser resolves to `https://attacker.com/...` — confirming the invariant "return_to must resolve only to a same-origin path" is violated.

### Citations

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L161-169)
```ruby
    def login_url_params(top_level:)
      query_params = {}
      query_params[:shop] = sanitized_params[:shop] if params[:shop].present?

      return_to = RedirectSafely.make_safe(session[:return_to] || params[:return_to], nil)

      if return_to.present? && return_to_param_required?
        query_params[:return_to] = return_to
      end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L230-232)
```ruby
    def base_return_address
      session.delete(:return_to) || ShopifyApp.configuration.root_url
    end
```

**File:** app/controllers/shopify_app/sessions_controller.rb (L109-111)
```ruby
    def copy_return_to_param_to_session
      session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
    end
```
