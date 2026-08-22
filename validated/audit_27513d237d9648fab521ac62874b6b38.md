### Title
Cross-shop / cross-user online session confusion via `shopify_user_id`-only keyed storage - (File: `lib/shopify_app/session/user_session_storage.rb`)

### Summary
`UserSessionStorage.store` and `.retrieve_by_shopify_user_id` key and load online-token sessions solely by `shopify_user_id`, with no `shopify_domain` scoping in the lookup/uniqueness. Combined with `SessionRepository.load_session` discarding the shop portion of the session id before calling this method, and `TokenExchange#authenticated_shopify_domain_from_token` letting the DB-derived `current_shopify_session.shop` override the freshly verified JWT `dest` claim, a stored user record can bind one shop's staff `shopify_user_id` to a different shop's `shopify_domain`/`shopify_token`.

### Finding Description
`store` does:
```ruby
user = find_or_initialize_by(shopify_user_id: user.id)
user.shopify_token = auth_session.access_token
user.shopify_domain = auth_session.shop
``` [1](#0-0) 
This unconditionally overwrites `shopify_domain`/`shopify_token` for whatever row already matches `shopify_user_id`, with no `shopify_domain` in the lookup key. `retrieve_by_shopify_user_id` mirrors this — it looks up `find_by(shopify_user_id: user_id)` only, and `construct_session` builds the returned `ShopifyAPI::Auth::Session` purely from that row's stored `shopify_domain`/`shopify_token`, with no re-check against the caller's verified shop. [2](#0-1) 

The loader that reaches this code intentionally discards the shop-binding portion of the session id:
```ruby
user = id.split("_").last
retrieve_user_session_by_shopify_user_id(user)
``` [3](#0-2) 
even though the session id itself was derived from the cryptographically verified JWT via `ShopifyAPI::Utils::SessionUtils.session_id_from_shopify_id_token` (which binds `sub` and `dest` together). [4](#0-3) 

The controller then re-derives the "authenticated" shop with the weaker, DB-sourced value taking priority over the freshly verified one:
```ruby
def authenticated_shopify_domain_from_token
  current_shopify_session&.shop || jwt_shopify_domain
end
``` [5](#0-4) 
`jwt_shopify_domain` is the value freshly verified from the signed id token on the current request. [6](#0-5) 
Because `current_shopify_session&.shop` (untrusted, keyed only by `shopify_user_id`) is checked first, a poisoned/aliased row can silently override the verified domain. `reject_mismatched_requested_shopify_domain` only catches this if `params[:shop]` is present and differs from the (wrong) authenticated domain; if the request omits `shop` (common on many API/webhook-adjacent routes) the check is skipped entirely. [7](#0-6) 

Root cause: the storage layer treats `shopify_user_id` as a global unique key across all installed shops instead of scoping the uniqueness/lookup to `(shopify_domain, shopify_user_id)`. Since a public multi-tenant Shopify app serves many independent shops, and each shop's staff `associated_user.id` values are small, shop-local sequential integers, collisions across unrelated shops are realistic — any later `store` call for a colliding id from one shop overwrites the `shopify_domain`/`shopify_token` for a different shop's previously stored row, and any subsequent `retrieve_by_shopify_user_id`/`construct_session` for that id will hand back the wrong shop's access token bound to the querying request's user/session.

### Impact Explanation
This causes cross-shop/cross-user session confusion: a request that verifiably authenticates for shop B via a valid signed id token can be handed a `ShopifyAPI::Auth::Session` carrying shop A's `access_token` because `retrieve_by_shopify_user_id`/`construct_session` never validate that the stored `shopify_domain` matches the domain asserted by the current verified JWT. `ShopifyAPI::Context.activate_session` then operates the Admin API call under the wrong shop's token, i.e., acting as another shop/tenant with its stolen token. This matches the "cross-user / cross-shop session confusion (acting as another user)" impact class.

### Likelihood Explanation
Exploitability depends on a `shopify_user_id` collision occurring between two different shops on a shared multi-tenant deployment of the app (a common Shopify app architecture), which is plausible given per-shop sequential numeric staff IDs. It does not require any secret, host misconfiguration, or victim action beyond both shops normally installing/using the app; the confusion is a direct consequence of the storage/lookup design rather than a crafted single request. This makes it a design-level, systemic weakness rather than a per-request forgeable exploit — feasibility is contingent on ID collisions actually occurring in a given deployment's data.

### Recommendation
Scope both the write and read paths of `UserSessionStorage` by the tuple `(shopify_domain, shopify_user_id)` instead of `shopify_user_id` alone:
- `store`: `find_or_initialize_by(shopify_user_id: user.id, shopify_domain: auth_session.shop)`.
- `retrieve_by_shopify_user_id`: accept/require the expected shop domain and `find_by(shopify_user_id: user_id, shopify_domain: expected_domain)`, returning `nil` on mismatch.
- In `SessionRepository.load_session`, preserve and pass through the shop portion of the id (not just `id.split("_").last`) so the user-session lookup can be domain-scoped.
- In `TokenExchange#authenticated_shopify_domain_from_token`, prefer the freshly verified `jwt_shopify_domain` over the DB-loaded `current_shopify_session&.shop`, or explicitly assert they match and reject on mismatch regardless of whether `params[:shop]` was supplied.

### Proof of Concept
```ruby
# test/shopify_app/session/user_session_storage_test.rb (illustrative)
test "retrieve_by_shopify_user_id can return a session for the wrong shop after a colliding store" do
  # Shop A's staff member (user_id 42) authorizes first
  UserMockSessionStore.store(
    mock_session(shop: "shop-a.myshopify.com"),
    mock_associated_user(id: 42),
  )

  # Shop B's staff member happens to have the same numeric id (42) and authorizes later
  UserMockSessionStore.store(
    mock_session(shop: "shop-b.myshopify.com"),
    mock_associated_user(id: 42),
  )

  # A request verified (via signed JWT) as belonging to shop A, sub=42, now resolves to shop B's token
  session = UserMockSessionStore.retrieve_by_shopify_user_id(42)
  assert_equal "shop-b.myshopify.com", session.shop # expected: should still be shop-a's session/blocked, not silently shop-b
end
```
This demonstrates that `store`/`retrieve_by_shopify_user_id` never scope by shop domain, so the second shop's write silently clobbers the first, and any verified request for shop A's user 42 will be handed shop B's token via `construct_session`.

### Citations

**File:** lib/shopify_app/session/user_session_storage.rb (L13-17)
```ruby
      def store(auth_session, user)
        user = find_or_initialize_by(shopify_user_id: user.id)
        user.shopify_token = auth_session.access_token
        user.shopify_domain = auth_session.shop

```

**File:** lib/shopify_app/session/user_session_storage.rb (L35-64)
```ruby
      def retrieve_by_shopify_user_id(user_id)
        user = find_by(shopify_user_id: user_id)
        construct_session(user)
      end

      def destroy_by_shopify_user_id(user_id)
        destroy_by(shopify_user_id: user_id)
      end

      private

      def construct_session(user)
        return unless user

        associated_user = ShopifyAPI::Auth::AssociatedUser.new(
          id: user.shopify_user_id,
          first_name: "",
          last_name: "",
          email: "",
          email_verified: false,
          account_owner: false,
          locale: "",
          collaborator: false,
        )

        session_attrs = {
          shop: user.shopify_domain,
          access_token: user.shopify_token,
          associated_user: associated_user,
        }
```

**File:** lib/shopify_app/session/session_repository.rb (L73-77)
```ruby
        else
          user = id.split("_").last
          ShopifyApp::Logger.debug("Loading session by user_id - user: #{user}")
          retrieve_user_session_by_shopify_user_id(user)
        end
```

**File:** lib/shopify_app/controller_concerns/token_exchange.rb (L44-49)
```ruby
    def current_shopify_session_id
      @current_shopify_session_id ||= ShopifyAPI::Utils::SessionUtils.session_id_from_shopify_id_token(
        id_token: shopify_id_token,
        online: online_token_configured?,
      )
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

**File:** lib/shopify_app/controller_concerns/with_shopify_id_token.rb (L19-25)
```ruby
    def jwt_shopify_domain
      return @jwt_shopify_domain if defined?(@jwt_shopify_domain)

      @jwt_shopify_domain = if jwt_payload.present?
        ShopifyApp::Utils.sanitize_shop_domain(jwt_payload.shopify_domain)
      end
    end
```
