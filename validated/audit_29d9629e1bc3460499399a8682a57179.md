### Title
Cross-shop session/token confusion via `shopify_user_id`-only keyed session storage that overwrites the prior shop's credentials without domain scoping - (File: `lib/shopify_app/session/user_session_storage.rb`)

### Summary
`ShopifyApp::UserSessionStorage.store` persists online (user-based) sessions keyed **only** by the global Shopify `shopify_user_id`, silently overwriting the previously stored `shopify_domain`/`shopify_token` for that user whenever the same Shopify user authenticates on a *different* shop. Session retrieval (`retrieve_user_session_by_shopify_user_id`) is likewise scoped only by user id, not by shop. Combined with `ShopifyApp::TokenExchange#reject_mismatched_requested_shopify_domain`, which skips the shop-domain consistency check whenever the caller-supplied `shop` param is blank, a request can be authenticated and have its session activated against a *stale* shop-domain/access-token pair belonging to a completely different store—mirroring the reported bug class where a residual authorization ("approved creditor") tied to a prior context is not invalidated after the account/context transfers to a new owner.

### Finding Description
`UserSessionStorage.store` does this: [1](#0-0) 

```
def store(auth_session, user)
  user = find_or_initialize_by(shopify_user_id: user.id)
  user.shopify_token = auth_session.access_token
  user.shopify_domain = auth_session.shop
  ...
  user.save!
  user.id
end
``` [1](#0-0) 

There is exactly one DB row per `shopify_user_id`. Because a Shopify user's numeric id is global (the same staff/user id is used across every store the person can access, e.g. as a collaborator, org member, or through Shopify Plus/organization access), completing OAuth/token-exchange for that user on **any** shop overwrites `shopify_domain` and `shopify_token` for **every** shop that user has previously authenticated to in this app. Retrieval is symmetric and also unscoped by shop: [2](#0-1) 

The session id used to look these rows up (`current_shopify_session_id`) is derived purely from the JWT's `sub` claim (user id), not the shop domain: [3](#0-2) 

The only defense against loading a stale, wrong-shop session is `reject_mismatched_requested_shopify_domain`, but it is a no-op whenever the caller does not pass an explicit `shop` param (which many endpoints reached purely via the `Authorization: Bearer <id_token>` header do not require, since the shop is otherwise conveyed by the JWT itself): [4](#0-3) 

```
def reject_mismatched_requested_shopify_domain
  requested_domain = requested_shopify_domain
  return false if requested_domain.blank?
  ...
end
``` [4](#0-3) 

So the flow is:
1. A shared Shopify user (e.g. a staff/collaborator account) completes the embedded app's token-exchange flow on Shop A → row for `shopify_user_id=X` stores `shopify_domain=A`, `shopify_token=tokenA`.
2. The same user id later authenticates via token exchange on Shop B (this can be an attacker-controlled shop that has added the victim as staff/collaborator, or any store the same real person legitimately uses) → the *same row* is overwritten with `shopify_domain=B`, `shopify_token=tokenB` via `ShopifyApp::Auth::TokenExchange#perform` → `SessionRepository.store_session` → `user_storage.store`.
3. Any subsequent request bearing a valid id token for user X issued for Shop A, but that does not carry an explicit `shop` request parameter, loads the session via `current_shopify_session` → `SessionRepository.load_session("online_X")` → returns the overwritten row now pointing at Shop B/`tokenB`.
4. `reject_mismatched_requested_shopify_domain` is skipped because `requested_domain` is blank, so `ShopifyAPI::Context.activate_session(current_shopify_session)` activates the wrong-shop session, and the app backend proceeds to serve/act using Shop B's access token in a request context that was authenticated (correctly, at the JWT layer) for Shop A.

This is directly analogous to the reported issue: a secondary/backup authorization (`approvedCreditor` there; the shared `shopify_user_id`-keyed token row here) is not invalidated/reset when the "ownership" context changes (account transfer there; shop-switch for the same global user id here), creating a backdoor that lets one context's stale credential act on behalf of another.

### Impact Explanation
An app backend integration that relies on `ShopifyApp::TokenExchange`/`LoginProtection` for authenticated user-token API calls, and that does not always forward an explicit `shop` parameter (common for endpoints authenticated purely via the `Authorization: Bearer <id_token>` header, background jobs, or SDK convenience wrappers), can silently execute Admin API operations against the wrong shop using a stale access token. Because Shopify staff/collaborator ids are shared across shops, an attacker with the ability to have the same real user authenticate on an attacker-controlled shop (e.g., inviting them as collaborator/staff) can pollute the shared row and subsequently ride on the resulting cross-shop token confusion, or more critically, on the other side, a request meant to hit Shop B could execute with Shop A's leftover access token, exposing that merchant's data/actions to an app-side inconsistency across tenants (shop isolation break).

### Likelihood Explanation
Medium: it requires (a) `user_session_repository` (online-token/user-based) configured with the default `UserSessionStorage` mixin, (b) the same Shopify user id legitimately or maliciously authenticating on more than one shop through this app, and (c) at least one authenticated code path that omits the `shop` param while relying on `TokenExchange#activate_shopify_session`. All are realistic, non-exotic configurations documented and supported by this gem (`config.user_session_repository = 'User'`), and Shopify explicitly supports one person having staff/collaborator access to multiple stores.

### Recommendation
Scope the user-session storage compound key by `(shopify_user_id, shopify_domain)` instead of `shopify_user_id` alone in `lib/shopify_app/session/user_session_storage.rb`, and/or always validate that the loaded session's `shop` matches the domain asserted by the current request's JWT (not only the optional `shop` param) before activating it in `lib/shopify_app/controller_concerns/token_exchange.rb#reject_mismatched_requested_shopify_domain`, i.e., fail closed when `requested_domain` is blank rather than skipping the check.

### Proof of Concept
1. Configure the app with `config.user_session_repository = 'User'` and the new embedded/token-exchange auth strategy.
2. Have Shopify user `X` complete token exchange while installed/embedded in Shop A → `Users` row: `shopify_user_id=X, shopify_domain=A, shopify_token=tokenA`.
3. Add user `X` as staff/collaborator on attacker-controlled Shop B; have them (or trick an automated flow) complete token exchange there too → same row is overwritten to `shopify_domain=B, shopify_token=tokenB` (`lib/shopify_app/session/user_session_storage.rb:12-28`).
4. From Shop A's embedded frontend, call a backend endpoint that authenticates via `Authorization: Bearer <id_token issued for Shop A>` but does not pass a `shop` query/body parameter.
5. `TokenExchange#activate_shopify_session` loads session id `"online_X"`, resolves to the row now containing Shop B's domain/token, and `reject_mismatched_requested_shopify_domain` short-circuits (`requested_domain.blank?` is true), so `ShopifyAPI::Context.activate_session` activates Shop B's session/token even though the request was authenticated for Shop A.

### Citations

**File:** lib/shopify_app/session/user_session_storage.rb (L12-28)
```ruby
    class_methods do
      def store(auth_session, user)
        user = find_or_initialize_by(shopify_user_id: user.id)
        user.shopify_token = auth_session.access_token
        user.shopify_domain = auth_session.shop

        if user.has_attribute?(:access_scopes)
          user.access_scopes = auth_session.scope.to_s
        end

        if user.has_attribute?(:expires_at)
          user.expires_at = auth_session.expires
        end

        user.save!
        user.id
      end
```

**File:** lib/shopify_app/session/user_session_storage.rb (L35-38)
```ruby
      def retrieve_by_shopify_user_id(user_id)
        user = find_by(shopify_user_id: user_id)
        construct_session(user)
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
