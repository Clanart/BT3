## Title
Raw Shopify session ID token is written to error logs during token exchange, leaking a bearer credential - (File: lib/shopify_app/auth/token_exchange.rb)

### Summary
`ShopifyApp::Auth::TokenExchange#exchange_token` logs the full, raw session ID token (`id_token`) at `error` level whenever Shopify's token-exchange endpoint reports it as invalid. This reproduces the exact bug class flagged in the referenced report — "Do not leak credentials and key material in debug-mode, to local log-output or external log aggregators" — except the leaked secret here is the Shopify session (ID) token instead of an ethereum private key.

### Finding Description
The token-exchange flow is triggered from `ShopifyApp::TokenExchange#activate_shopify_session`, which any unauthenticated or unrelated caller can reach by sending a request with an `Authorization: Bearer <id_token>` header (or `id_token` param) to any controller that includes this concern: [1](#0-0) 

When `current_shopify_session` is blank, it calls `retrieve_session_from_token_exchange`, which invokes `ShopifyApp::Auth::TokenExchange.perform(shopify_id_token)`: [2](#0-1) 

Inside `exchange_token`, if Shopify's API responds that the token is invalid, the raw `id_token` string is interpolated directly into the log message at `error` level: [3](#0-2) 

```ruby
rescue ShopifyAPI::Errors::InvalidJwtTokenError
  Logger.error("Invalid id token '#{id_token}' during token exchange")
  raise
```

This is precisely the pattern the external report calls out (`txargs.privateKey` leaking to log output / remote aggregators): a security-sensitive bearer token that authenticates a merchant/user session is written verbatim to application logs. This is functionally equivalent in every gem deployment, whether logs go to STDOUT, Rails.logger, or a remote log aggregator (e.g. Sentry-like services), and it is triggered by data supplied on an inbound HTTP request rather than by developer action.

### Impact Explanation
A Shopify session (ID) token is a signed JWT that, while short-lived, is a bearer credential — anyone who obtains a valid, unexpired one can use it (via token exchange) to obtain an offline/online access token for the shop it was issued to. Persisting these tokens in plaintext log streams increases the attack surface for token theft: logs are frequently shipped to third-party aggregators, stored with looser access controls than the application database, or retained for extended windows — all in direct violation of the "do not leak credentials" and "keys should be protected in memory and only decrypted/used for the duration needed" guidance from the reference report.

### Likelihood Explanation
The code path is reachable by any request that supplies an ID token, and simply requires the token to fail Shopify's own signature/expiry validation (e.g., token has expired between issuance and use, or is malformed/tampered) — a common, easily triggerable condition, not requiring any privileged access. An attacker who wants to force this log line only needs to send any expired/invalid JWT string in the standard `Authorization: Bearer` header to a `TokenExchange`-protected endpoint.

### Recommendation
- Do not interpolate the raw `id_token` value into any log statement. Log only non-sensitive metadata (e.g., shop domain, error class, token exp claim) or a truncated/hashed fingerprint of the token.
- Apply the same review to the sibling `Logger.error` message that includes `error.response.body` on `HttpResponseError`, since Shopify's error responses could also echo back sensitive request data.
- Audit other log statements throughout `lib/shopify_app` for similar interpolation of tokens/secrets (e.g., `shopify_id_token`, `access_token`, `secret`).

### Proof of Concept
1. In an app using `ShopifyApp::TokenExchange`, send a request to a protected controller action with header `Authorization: Bearer <expired-or-malformed-JWT>`.
2. `activate_shopify_session` finds no existing session and calls `retrieve_session_from_token_exchange`, which calls `ShopifyAPI::Auth::TokenExchange.exchange_token` with the supplied token.
3. Shopify's API rejects the invalid token, raising `ShopifyAPI::Errors::InvalidJwtTokenError`.
4. `exchange_token` catches this and executes `Logger.error("Invalid id token '#{id_token}' during token exchange")`, writing the full raw token string to the application's log output/aggregator.

### Citations

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L19-32)
```ruby
    def activate_shopify_session(&block)
      retrieve_session_from_token_exchange if current_shopify_session.blank? || should_exchange_expired_token?

      return if reject_mismatched_requested_shopify_domain

      ShopifyApp::Logger.debug("Activating Shopify session")
      ShopifyAPI::Context.activate_session(current_shopify_session)
      with_token_refetch(current_shopify_session, shopify_id_token, &block)
    rescue *INVALID_SHOPIFY_ID_TOKEN_ERRORS => e
      respond_to_invalid_shopify_id_token(e)
    ensure
      ShopifyApp::Logger.debug("Deactivating session")
      ShopifyAPI::Context.deactivate_session
    end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L85-88)
```ruby
    def retrieve_session_from_token_exchange
      @current_shopify_session = nil
      ShopifyApp::Auth::TokenExchange.perform(shopify_id_token)
    end
```

**File:** lib/shopify_app/auth/token_exchange.rb (L42-59)
```ruby
      def exchange_token(shop:, id_token:, requested_token_type:)
        session = ShopifyAPI::Auth::TokenExchange.exchange_token(
          shop: shop,
          session_token: id_token,
          requested_token_type: requested_token_type,
        )

        SessionRepository.store_session(session)

        session
      rescue ShopifyAPI::Errors::InvalidJwtTokenError
        Logger.error("Invalid id token '#{id_token}' during token exchange")
        raise
      rescue ShopifyAPI::Errors::HttpResponseError => error
        Logger.error(
          "A #{error.code} error (#{error.class}) occurred during the token exchange. Response: #{error.response.body}",
        )
        raise
```
