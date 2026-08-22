### Title
Callback redirect accepts arbitrary URL schemes for `return_to` before `redirect_to(..., allow_other_host: true)` - (File: `app/controllers/shopify_app/callback_controller.rb`)

### Summary
`CallbackController#redirect_to_app` decides whether to redirect the browser directly to a session-stored `return_to` value (with `allow_other_host: true`) based on `fully_formed_url?`, which only checks that a scheme and host are present — it never restricts the scheme to `http`/`https`, mirroring the reported `bypassSecurityTrustResourceUrl` bug class (untrusted URL trusted without a protocol allow-list).

### Finding Description
In the OAuth callback flow, after a successful token exchange the controller redirects the merchant back into the app: [1](#0-0) 

`fully_formed_url?` is the only gate before `return_to` is used verbatim as the redirect target with `allow_other_host: true`, and it merely checks `uri.scheme.present? && uri.host.present?` — any scheme (not just `http://`/`https://`) satisfies this check.

`return_to` is populated in `session[:return_to]` from attacker/merchant-influenced input in two places:
- `SessionsController#copy_return_to_param_to_session`, which takes `params[:return_to]` through the external `RedirectSafely.make_safe` helper before storing it: [2](#0-1) 
- `LoginProtection#login_url_params`, which re-reads `session[:return_to] || params[:return_to]` through `RedirectSafely.make_safe` again when building the login URL: [3](#0-2) 

The actual scheme-restriction behavior depends entirely on the external `redirect_safely` gem (`RedirectSafely.make_safe`), which is a dependency not present in this repository's source, so I cannot verify from this codebase whether it strips or rejects non-`http(s)` schemes (e.g. `javascript:`, `data:`) before the value reaches `session[:return_to]`. Per the analysis rules, dependency-only behavior cannot be used as proof of an in-scope vulnerability, and this is the only path by which a non-http(s) scheme could reach `fully_formed_url?`/`redirect_to_app`.

### Impact Explanation
If `RedirectSafely.make_safe` does not enforce an `http`/`https` scheme allow-list (unverifiable from this repo), an attacker could craft a login link with `return_to=<non-http-scheme-url>` that, after a legitimate merchant completes OAuth, causes the browser to be redirected via `redirect_to(..., allow_other_host: true)` to a non-`http(s)` URI. Depending on scheme and browser handling this could range from no impact (browsers generally refuse to navigate to `javascript:`/`data:` via `Location` header redirects) up to open-redirect-style abuse if the scheme is otherwise browser-navigable.

### Likelihood Explanation
Low/uncertain. Exploitability is gated entirely by the external `RedirectSafely.make_safe` sanitizer, which almost certainly exists specifically to prevent this class of issue (open redirects), and modern browsers do not execute script via `Location`-header redirects to `javascript:`/`data:` URIs. I could not confirm from the in-scope repository whether the gem enforces a scheme allow-list, so I cannot prove the root cause is exploitable end-to-end within this codebase alone.

### Recommendation
Regardless of what `RedirectSafely.make_safe` does today, harden `fully_formed_url?` in `app/controllers/shopify_app/callback_controller.rb` to explicitly allow-list `http`/`https` schemes only, rather than relying on scheme/host presence:
```ruby
def fully_formed_url?(return_to)
  uri = Addressable::URI.parse(return_to)
  uri.present? && %w[http https].include?(uri.scheme) && uri.host.present?
end
```
This removes the dependency on an external gem's behavior for this specific decision and closes the gap directly at the point where `allow_other_host: true` is used.

### Proof of Concept
Not confirmed as reachable/exploitable within this repository alone: the only user-influenced path to a non-`http(s)`-scheme `return_to` value passes through the external `RedirectSafely.make_safe` sanitizer [2](#0-1) , whose scheme-filtering logic is not part of this codebase and could not be verified. Without confirming that gem allows non-http(s) schemes through, I cannot construct a concrete end-to-end PoC that proves exploitability, only the missing in-repo defense-in-depth in `fully_formed_url?` [4](#0-3) .

### Citations

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

**File:** app/controllers/shopify_app/sessions_controller.rb (L109-111)
```ruby
    def copy_return_to_param_to_session
      session[:return_to] = RedirectSafely.make_safe(params[:return_to], "/") if params[:return_to]
    end
```

**File:** lib/shopify_app/controller_concerns/login_protection.rb (L161-187)
```ruby
    def login_url_params(top_level:)
      query_params = {}
      query_params[:shop] = sanitized_params[:shop] if params[:shop].present?

      return_to = RedirectSafely.make_safe(session[:return_to] || params[:return_to], nil)

      if return_to.present? && return_to_param_required?
        query_params[:return_to] = return_to
      end

      has_referer_shop_name = referer_sanitized_shop_name.present?

      if has_referer_shop_name
        query_params[:shop] ||= referer_sanitized_shop_name
      end

      if params[:host].present?
        query_params[:host] ||= host
      end

      if params[:access_scopes].present?
        query_params[:scope] = params[:access_scopes].join(",")
      end

      query_params[:top_level] = true if top_level
      query_params
    end
```
