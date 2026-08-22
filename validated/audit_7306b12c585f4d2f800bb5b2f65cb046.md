### Title
Open Redirect in OAuth Callback due to Phishing Check Bypass When `return_to` Is a Fully-Formed URL - (File: `app/controllers/shopify_app/callback_controller.rb`)

### Summary
`CallbackController#redirect_to_app` contains the same class of bug as the referenced report: a validation ("threshold check") is skipped whenever an alternate/edge-case branch is taken, because the check is performed against the wrong variable. When `return_to` is a fully-formed URL, the anti-phishing validation (`deduced_phishing_attack?`) still only validates `decoded_host` (derived from the `host` param) — never the actual `return_to` value that is used for the redirect — allowing the check to be trivially satisfied while the dangerous value goes unchecked.

### Finding Description
In `redirect_to_app`: [1](#0-0) 

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

`deduced_phishing_attack?` never inspects `return_to`; it exclusively checks `decoded_host`, which is derived from `params[:host]`: [2](#0-1) 

```ruby
def fully_formed_url?(return_to)
  uri = Addressable::URI.parse(return_to)
  uri.present? && uri.scheme.present? && uri.host.present?
end

def decoded_host
  @decoded_host ||= ShopifyAPI::Auth.embedded_app_url(params[:host])
end

def deduced_phishing_attack?
  sanitized_host = ShopifyApp::Utils.sanitize_shop_domain(URI(decoded_host).host)
  ...
  sanitized_host.nil?
end
```

This mirrors the PrizePool bug pattern precisely: an alternate code path (`fully_formed_url?(return_to)` being true, analogous to `_nextNumberOfTiers >= MAXIMUM_NUMBER_OF_TIERS`) causes the value that is actually used (`return_to`, analogous to the new tier count) to bypass the intended security check (`deduced_phishing_attack?`, analogous to the claim-count threshold check), because the check is evaluated against an unrelated variable (`decoded_host`, i.e. the `host` param) instead of the value being trusted (`return_to`).

`session[:return_to]` is attacker-influenceable: elsewhere in the same concern, the code explicitly treats `session[:return_to] || params[:return_to]` as user input requiring sanitization via `RedirectSafely.make_safe`: [3](#0-2) 

```ruby
def login_url_params(top_level:)
  query_params = {}
  query_params[:shop] = sanitized_params[:shop] if params[:shop].present?

  return_to = RedirectSafely.make_safe(session[:return_to] || params[:return_to], nil)
  ...
```

This confirms `session[:return_to]` is expected to hold attacker-controllable data and normally requires `RedirectSafely` sanitization before use — sanitization that `redirect_to_app` skips entirely in the `fully_formed_url?` branch.

### Impact Explanation
If an attacker can get an absolute, fully-formed URL (e.g., `https://evil.example.com/phish`) stored into `session[:return_to]` prior to the OAuth callback completing, `redirect_to_app` will redirect the merchant's browser to that arbitrary host with `allow_other_host: true`, immediately after a successful OAuth install/login flow. This is a classic OAuth-callback open redirect, which can be leveraged for phishing (stealing merchant session/credentials by redirecting to a lookalike page right after a trusted install flow) or for token/code leakage if any sensitive parameters are appended to the redirect target.

### Likelihood Explanation
Exploitability depends on whether an unauthenticated/unrelated actor can influence `session[:return_to]` with a fully-formed external URL before the callback fires (e.g., via the initial `/login?return_to=...` or `/auth?return_to=...` entry point). The codebase’s own sanitization logic elsewhere (`RedirectSafely.make_safe`) implies this value is treated as untrusted user input in other contexts, but I was not able to fully verify, within this session, the exact code in `app/controllers/shopify_app/sessions_controller.rb` that first assigns `session[:return_to]`, due to a tool access issue in the final iteration. This is a gap in my verification: confirming whether `session[:return_to]` is sanitized (e.g., via `RedirectSafely`) at the point of assignment, versus only when building the `/login` redirect URL, would determine whether this is directly exploitable by an anonymous request or only reachable through a more constrained input path.

### Recommendation
Validate `return_to` itself (not `decoded_host`) whenever `fully_formed_url?(return_to)` is true — e.g., require that `return_to`'s host matches a trusted Shopify/admin domain via `ShopifyApp::Utils.sanitize_shop_domain`, or apply `RedirectSafely.make_safe` against the actual redirect target before it is used, consistent with how `login_url_params` already treats this value.

### Proof of Concept
1. Cause `session[:return_to]` to be set to an absolute external URL (e.g., through the login/auth entry point that persists `params[:return_to]` into `session[:return_to]`).
2. Complete the OAuth callback (`GET /auth/shopify/callback`) with valid `code`, `hmac`, `shop`, and a `host` param that decodes to a legitimate/trusted shop domain (so `deduced_phishing_attack?` returns `false`).
3. `redirect_to_app` detects `fully_formed_url?(return_to)` is `true`, sets `redirect_to = return_to` (the attacker's external URL), and since `deduced_phishing_attack?` only validated the unrelated `decoded_host`, it does not override `redirect_to`.
4. The browser is redirected to the attacker-controlled URL via `redirect_to(redirect_to, allow_other_host: true)`. [1](#0-0)

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

**File:** app/controllers/shopify_app/callback_controller.rb (L96-113)
```ruby
    def fully_formed_url?(return_to)
      uri = Addressable::URI.parse(return_to)
      uri.present? && uri.scheme.present? && uri.host.present?
    end

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
