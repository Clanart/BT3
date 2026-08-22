### Title
Cross-shop session/token leakage due to online user sessions keyed only by `shopify_user_id`, discarding shop context - ([File: lib/shopify_app/session/session_repository.rb], [File: lib/shopify_app/session/user_session_storage.rb])

### Summary
Online (user-based) session storage and retrieval in `shopify_app` is scoped solely by the global Shopify `shopify_user_id`, not by the shop the session belongs to. When the same underlying Shopify staff account authenticates for a second shop, the stored record for that `shopify_user_id` is overwritten with the new shop's domain and access token. Any subsequent request resolved for the *original* shop but the *same* `shopify_user_id` (e.g. a bearer-token-only API call lacking a `shop` query param) will load and activate the wrong shop's session — a cross-tenant credential leak analogous to the reported "stale permissions still attached after transfer" bug class, where a mapping keyed only by the "handler"/actor identity (here `shopify_user_id`) is not properly scoped by the current "owner" (here the shop).

### Finding Description
`ShopifyApp::UserSessionStorage.store` looks up and persists the session record keyed exclusively by `shopify_user_id`, then unconditionally overwrites `shopify_domain` and `shopify_token` with whatever shop/token was just exchanged: [1](#0-0) 

`ShopifyApp::SessionRepository#load_session` (and `#delete_session`) similarly discard the shop portion of the composite session id for online sessions, extracting and using only the trailing `user_id` segment to look up the stored session: [2](#0-1) 

`retrieve_user_session_by_shopify_user_id` then queries storage purely by that global id: [3](#0-2) [4](#0-3) 

Session ids for online sessions are composed as `"{shop}_{user_id}"`, confirmed by test fixtures: [5](#0-4) 

So if the same Shopify user (same `shopify_user_id`, e.g. a staff member/collaborator with access to more than one shop that installs this app) triggers a token exchange for Shop B after previously having an online session stored for Shop A, `UserSessionStorage.store` overwrites the single row keyed by that user id with Shop B's `shopify_domain`/`shopify_token`. A later request carrying a valid Shopify-issued id token for Shop A (same `shopify_user_id`) will compute a session id like `"shopA.myshopify.com_123"`, but `load_session` strips the shop prefix and calls `retrieve_user_session_by_shopify_user_id("123")`, returning the record now pointing to Shop B's domain and access token.

The only guard against this is `reject_mismatched_requested_shopify_domain` in the token-exchange flow, which compares the loaded session's `.shop` to `params[:shop]`: [6](#0-5) 
This check is skipped entirely when `params[:shop]` is blank (`return false if requested_domain.blank?`), which is common for backend/XHR calls authenticated purely via the `Authorization: Bearer <id_token>` header without a `shop` query parameter — a normal, unprivileged usage pattern for App Bridge-driven requests.

### Impact Explanation
When the guard is bypassed, `ShopifyAPI::Context.activate_session(current_shopify_session)` activates a session object bound to a *different* shop's domain/access token while serving a request that was authenticated (via a valid id token) for the original shop. This can result in a merchant's app-backend request unintentionally operating with another shop's access token, i.e., cross-shop token exposure and potential cross-tenant data access — without requiring any secret compromise, purely as a side effect of the storage key design.

### Likelihood Explanation
This requires the same Shopify user id to have legitimately authenticated the app for two different shops (e.g. a staff/collaborator account, or a Plus organization with multiple shops under shared staff), which is a realistic and common scenario for embedded apps, and requires no privileged action beyond normal login flows plus one unprivileged request lacking the `shop` param — a routine condition for many backend API calls.

### Recommendation
Scope online user session storage and lookup by both `shop` and `shopify_user_id` (composite key), instead of `shopify_user_id` alone, mirroring the shop-scoped `offline_` session handling already present for offline sessions. `load_session`/`delete_session` should retain and use the shop segment of the session id rather than discarding it, and `UserSessionStorage.store`/`retrieve_by_shopify_user_id` should require/validate the shop domain as part of the lookup key.

### Proof of Concept
1. Staff user `U` (Shopify `shopify_user_id = 123`) installs/authenticates the embedded app on Shop A, completing token exchange; `UserSessionStorage.store` persists a row: `shopify_user_id=123, shopify_domain=shopA, shopify_token=tokenA`.
2. The same user `U` later authenticates the app on Shop B (e.g. as a collaborator); `UserSessionStorage.store` finds the *same* row by `shopify_user_id=123` and overwrites it: `shopify_domain=shopB, shopify_token=tokenB`.
3. A backend request tied to Shop A's context sends `Authorization: Bearer <id_token issued for shop A, sub=123>` without a `shop` query param (a normal XHR pattern).
4. `current_shopify_session_id` resolves to `"shopA_123"`; `SessionRepository#load_session` strips the shop, calls `retrieve_user_session_by_shopify_user_id("123")`, and gets back the row now containing `shopB`/`tokenB`.
5. Since `params[:shop]` is blank, `reject_mismatched_requested_shopify_domain` returns `false` (no mismatch check performed), and `ShopifyAPI::Context.activate_session` activates Shop B's token for what was intended to be a Shop A request.

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

**File:** lib/shopify_app/session/session_repository.rb (L22-24)
```ruby
      def retrieve_user_session_by_shopify_user_id(user_id)
        user_storage.retrieve_by_shopify_user_id(user_id)
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

**File:** test/shopify_app/auth/token_exchange_test.rb (L199-206)
```ruby
    ShopifyAPI::Auth::Session.new(
      id: "#{shop}_#{user_id}",
      shop: shop,
      is_online: true,
      access_token: "online-token",
      associated_user: user,
    )
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
