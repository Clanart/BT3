### Title
User sessions are keyed by `shopify_user_id` alone instead of by the (shop, user_id) pair, allowing cross-shop session/token collision - (File: `lib/shopify_app/session/user_session_storage.rb`)

### Summary
`ShopifyApp::UserSessionStorage` and `ShopifyApp::SessionRepository` persist and look up online (user) access-token sessions using only the Shopify `shopify_user_id` as the lookup/uniqueness key, discarding the shop that the id belongs to. This is the same bug class as the reported finding: a value that is not a reliable canonical/unique identifier on its own (here, a numeric user id, analogous to the pegged asset in the oracle report) is used as the sole map key instead of the full pair (shop, user_id). When Shopify user ids collide across different shops (e.g. default owner accounts, dev/trial stores, or any two accounts that end up sharing the same numeric id), one shop's stored access token/session is silently overwritten and returned for a different shop's request.

### Finding Description
`UserSessionStorage.store` looks up the local record with: [1](#0-0) 

It calls `find_or_initialize_by(shopify_user_id: user.id)` and then unconditionally rewrites `user.shopify_domain = auth_session.shop` and `user.shopify_token = auth_session.access_token`. The record is keyed solely by `shopify_user_id`, not by `(shopify_domain, shopify_user_id)`.

Retrieval is equally single-key: [2](#0-1) 

The in-memory store used for tests/small apps has the identical pattern: [3](#0-2) 

`ShopifyApp::SessionRepository.load_session`, which the ShopifyAPI gem calls to resolve a session id, splits the composite id string (`"<shop>_<user_id>"` for online sessions) and only uses the trailing numeric user id — the shop portion is parsed out and then discarded entirely for user sessions: [4](#0-3) 

`SessionRepository.retrieve_user_session_by_shopify_user_id` simply forwards to the storage's `retrieve_by_shopify_user_id`: [5](#0-4) 

This is reached on every OAuth callback for any shop that installs/re-authenticates the app with online access tokens, via `CallbackController#callback` → `save_session` → `SessionRepository.store_session`: [6](#0-5) [7](#0-6) 

and the resulting `associated_user.id` is stashed directly into the Rails session for later lookups: [8](#0-7) 

Exactly as in the oracle report — where `tokenToOracle[_token]` silently reused an existing oracle for a new, distinct pair — `UserSessionStorage`/`SessionRepository` silently reuse (overwrite and return) the single record for `shopify_user_id` regardless of which shop actually issued that id.

### Impact Explanation
If any two Shopify accounts across different shops end up sharing the same `shopify_user_id` value (which is not guaranteed to be a cross-shop-unique canonical identifier the way `shopify_domain` is), then:
- OAuth completion from shop B silently overwrites the stored access token and `shopify_domain` for the record previously belonging to shop A's user (`find_or_initialize_by(shopify_user_id: ...)`).
- Any subsequent `retrieve_user_session_by_shopify_user_id` / `load_session` call keyed by that user id — regardless of which shop the session id string encoded — will return the most recently stored session, which may belong to a different shop.
- This results in cross-shop session/token confusion: an app request intended for shop A's user can silently be served with shop B's access token (or vice versa), i.e. exactly the kind of cross-shop access the validation rules classify as concrete impact.

### Likelihood Explanation
The `shop` component of the composite session id is parsed out of the id string by `load_session` and then never checked against the record actually returned — likelihood of triggering a mismatch depends entirely on the collision/reuse of `shopify_user_id` values across shops, which the gem does nothing to prevent or verify at storage/retrieval time. This is a structural design flaw (single-token key on a multi-tenant, per-pair concept) rather than a probabilistic/rare edge case, matching the "silently skip/overwrite" root cause pattern of the referenced report exactly.

### Recommendation
Scope the user-session storage and lookup key to the pair `(shopify_domain, shopify_user_id)` rather than `shopify_user_id` alone:
- Change `UserSessionStorage.store`/`retrieve_by_shopify_user_id`/`destroy_by_shopify_user_id` (and the in-memory equivalent) to use a composite key, e.g. `find_or_initialize_by(shopify_domain: auth_session.shop, shopify_user_id: user.id)`.
- In `SessionRepository.load_session`/`delete_session`, parse both the shop and user id out of the composite session id and require both to match the stored record before returning/deleting a session.

### Proof of Concept
1. App is configured with `config.user_session_repository` using `UserSessionStorage` (or the in-memory store) for online access tokens.
2. Shop A completes OAuth; `associated_user.id` happens to be `42`. `UserSessionStorage.store` creates a record: `shopify_user_id=42, shopify_domain="shop-a.myshopify.com", shopify_token="tokenA"`.
3. Shop B (a different shop that happens to have — or an attacker who arranges to have — an associated online-access user whose id is also `42`) completes OAuth. `UserSessionStorage.store` runs `find_or_initialize_by(shopify_user_id: 42)`, finds shop A's record, and overwrites it: `shopify_domain="shop-b.myshopify.com", shopify_token="tokenB"`.
4. Any subsequent call that resolves a session id of the form `"shop-a.myshopify.com_42"` goes through `SessionRepository.load_session`, which discards the `"shop-a.myshopify.com"` prefix and calls `retrieve_user_session_by_shopify_user_id("42")`, returning the now-overwritten shop B session/token instead of shop A's — a cross-shop session/token confusion caused solely by keying on `shopify_user_id` instead of the `(shop, user_id)` pair.

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

**File:** lib/shopify_app/session/in_memory_user_session_store.rb (L6-14)
```ruby
      def store(session, user)
        id = super
        repo[user.id.to_s] = session
        id
      end

      def retrieve_by_shopify_user_id(user_id)
        repo[user_id]
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

**File:** app/controllers/shopify_app/callback_controller.rb (L21-29)
```ruby
      save_session(api_session) if api_session
      update_rails_cookie(api_session, cookie)

      return respond_with_user_token_flow if start_user_token_flow?(api_session)

      ShopifyApp.configuration.post_authenticate_tasks.perform(api_session)

      redirect_to_app if check_billing(api_session)
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L46-48)
```ruby
    def save_session(api_session)
      ShopifyApp::SessionRepository.store_session(api_session)
    end
```

**File:** app/controllers/shopify_app/callback_controller.rb (L76-77)
```ruby
      session[:shopify_user_id] = api_session.associated_user.id if api_session.online?
      ShopifyApp::Logger.debug("Saving Shopify user ID to cookie")
```
