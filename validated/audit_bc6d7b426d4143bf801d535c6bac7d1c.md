### Title
Cross-Shop Session Confusion via Missing Shop-Association Check in User Session Storage/Retrieval - ([File: lib/shopify_app/session/user_session_storage.rb], [File: lib/shopify_app/controller_concerns/token_exchange.rb])

### Summary
The gem's online (user) session storage keys and retrieves records by `shopify_user_id` alone, with no binding to the shop the token was actually issued for. Combined with `TokenExchange#authenticated_shopify_domain_from_token`, which trusts the *stored* session's `shop` attribute over the verified ID token's own shop claim whenever a session record is found, this reproduces the reported bug class: two logically related entities (a user's access-token record and the shop it belongs to) are used together without validating their association, allowing state/session confusion across shops.

### Finding Description
`UserSessionStorage.store` finds or creates the user record solely by `shopify_user_id`, then overwrites its `shopify_domain` and `shopify_token` unconditionally: [1](#0-0) 

`retrieve_by_shopify_user_id` likewise looks the record up only by user id, without scoping to a shop: [2](#0-1) 

The same pattern exists in the deprecated `UserSessionStorageWithScopes`: [3](#0-2) 

Because a real Shopify user (`shopify_user_id`) can be a staff/collaborator on multiple installs of the same app across different shops, this single-row-per-user design means the second shop's install silently overwrites the first shop's stored `shop`/token association for that user, with no check that the two installs relate to the same tenant.

`SessionRepository.load_session` dispatches purely on the session id's shape (`offline_...` vs. a bare user id) and, for user/online sessions, calls `retrieve_user_session_by_shopify_user_id` with no shop cross-check: [4](#0-3) 

In `TokenExchange`, once a session is loaded this way, `authenticated_shopify_domain_from_token` prefers the loaded (potentially stale/foreign-shop) session's `shop` attribute over the shop claim in the freshly verified ID token, and only falls back to the token's own claim if no session was found at all: [5](#0-4) 

The consistency check that exists, `reject_mismatched_requested_shopify_domain`, is only enforced when the request explicitly carries a `shop` query parameter; if it's blank, the check short-circuits and the (possibly wrong-shop) session is activated as-is: [6](#0-5) [7](#0-6) 

This mirrors the reported bug class exactly: the code accepts two related identifiers (a verified per-request shop context and a stored session record) but never enforces that they refer to the same tenant, and the resulting cross-entity mismatch corrupts state (a wrong session gets treated as authenticated for the current shop) — the redemption-offer/request analog here is offer≈shop, request≈stored user session.

### Impact Explanation
If exploited, a request that is genuinely and validly authenticated for Shop A can end up operating with a session/access token belonging to Shop B (because the stored user record was last overwritten by Shop B's install for the same Shopify user id). This is a cross-shop confidentiality/integrity issue: subsequent `ShopifyAPI::Clients::*` calls made with `current_shopify_session` would read/write Shop B's data while the browser/UI context believes it is Shop A. When the `shop` param is present, the mismatch check instead causes a hard lockout (unauthorized) for the legitimate shop, which is a denial-of-service analog to "blocked redemptions" in the original report.

### Likelihood Explanation
Requires a real-world but common precondition: the same Shopify user (e.g., an agency/collaborator account) has authorized the same embedded app on two different shops using online (per-user) tokens, and the app relies on XHR/API requests that carry only the session/ID token without an explicit `shop` query parameter (a common pattern for authenticated fetches after initial page load). No attacker forgery of Shopify's signature is needed — only the app's own single-row-per-user storage design combined with the trust-order in `authenticated_shopify_domain_from_token`.

### Recommendation
- Scope user session storage/retrieval by both `shopify_user_id` and `shopify_domain` (or at minimum validate that the retrieved record's `shop` matches the shop claim in the currently verified ID token before treating it as "authenticated").
- In `authenticated_shopify_domain_from_token`, prefer/require the verified token's own shop claim (`jwt_shopify_domain`) and cross-check it against `current_shopify_session&.shop`, rejecting on mismatch even when `requested_shopify_domain` is blank, rather than only checking when a `shop` param is explicitly present.

### Proof of Concept
1. Shopify user `U` installs the app (online token) on Shop A → `UserSessionStorage.store` creates row `{shopify_user_id: U, shopify_domain: "shop-a.myshopify.com", shopify_token: tokenA}`.
2. The same user `U` (staff/collaborator) installs/authorizes the app on Shop B → `store` finds the same row by `shopify_user_id` only and overwrites it to `{shopify_domain: "shop-b.myshopify.com", shopify_token: tokenB}` [1](#0-0) .
3. Later, the merchant opens the embedded app inside Shop A's admin and the app issues an XHR request carrying a fresh, validly-signed session token (`dest`/shop = Shop A) but no `shop` query parameter.
4. `activate_shopify_session` loads the session via `current_shopify_session_id` → `SessionRepository.load_session` → `retrieve_user_session_by_shopify_user_id(U)`, returning the row now pointing at Shop B [4](#0-3) .
5. Because `requested_shopify_domain` is blank, `reject_mismatched_requested_shopify_domain` returns `false` without comparing against the token's real shop claim [6](#0-5) , and `ShopifyAPI::Context.activate_session` activates Shop B's session/token for what the browser/UI believes is a Shop A request [8](#0-7) .

### Citations

**File:** lib/shopify_app/session/user_session_storage.rb (L12-17)
```ruby
    class_methods do
      def store(auth_session, user)
        user = find_or_initialize_by(shopify_user_id: user.id)
        user.shopify_token = auth_session.access_token
        user.shopify_domain = auth_session.shop

```

**File:** lib/shopify_app/session/user_session_storage.rb (L35-38)
```ruby
      def retrieve_by_shopify_user_id(user_id)
        user = find_by(shopify_user_id: user_id)
        construct_session(user)
      end
```

**File:** lib/shopify_app/session/user_session_storage_with_scopes.rb (L19-28)
```ruby
      def store(auth_session, user)
        user = find_or_initialize_by(shopify_user_id: user.id)
        user.shopify_token = auth_session.access_token
        user.shopify_domain = auth_session.shop
        user.access_scopes = auth_session.scope.to_s
        user.expires_at = auth_session.expires

        user.save!
        user.id
      end
```

**File:** lib/shopify_app/session/session_repository.rb (L66-78)
```ruby
      # ShopifyAPI::Auth::SessionStorage override
      def load_session(id)
        match = id.match(/^offline_(.*)/)
        if match
          domain = match[1]
          ShopifyApp::Logger.debug("Loading session by domain - domain: #{domain}")
          retrieve_shop_session_by_shopify_domain(domain)
        else
          user = id.split("_").last
          ShopifyApp::Logger.debug("Loading session by user_id - user: #{user}")
          retrieve_user_session_by_shopify_user_id(user)
        end
      end
```

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

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L69-71)
```ruby
    def authenticated_shopify_domain_from_token
      current_shopify_session&.shop || jwt_shopify_domain
    end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L73-83)
```ruby
    def reject_mismatched_requested_shopify_domain
      requested_domain = requested_shopify_domain
      return false if requested_domain.blank?

      authenticated_domain = authenticated_shopify_domain_from_token
      return false if authenticated_domain.blank? || authenticated_domain == requested_domain

      ShopifyApp::Logger.debug("Shop context validation failed")
      head(:unauthorized)
      true
    end
```
