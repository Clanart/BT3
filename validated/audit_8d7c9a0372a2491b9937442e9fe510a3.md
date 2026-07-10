### Title
Non-Atomic Token Migration Swap Causes Irrecoverable Fund Loss When Recipient Unregistered on New Token - (File: near/omni-bridge/src/lib.rs)

### Summary

`swap_migrated_token` issues `burn` and `mint` as parallel, detached NEAR promises with no failure callback. `ft_on_transfer` simultaneously returns `U128(0)` (consuming all old tokens with no refund). If `mint` panics because the user has no storage deposit on the new token, the `burn` receipt executes independently and the user's old tokens are permanently destroyed with no new tokens issued.

### Finding Description

When a user sends old (migrated) tokens to the bridge with `msg: {"SwapMigratedToken": null}`, the following sequence occurs:

**Step 1 — `ft_on_transfer` immediately consumes all tokens:**

```rust
// near/omni-bridge/src/lib.rs:275-279
BridgeOnTransferMsg::SwapMigratedToken => {
    self.swap_migrated_token(sender_id, token_id, amount)
        .detach();                                    // ← promise result ignored
    PromiseOrValue::Value(U128(0))                   // ← 0 refund: all tokens consumed
}
```

**Step 2 — `swap_migrated_token` fires burn and mint in parallel:**

```rust
// near/omni-bridge/src/lib.rs:2749-2752
let burn = ext_token::ext(old_token).burn(amount);
let mint = ext_token::ext(new_token).mint(sender_id, amount, None);
burn.and(mint)   // ← two independent receipts, no rollback linkage
```

**Step 3 — `mint` panics if recipient is unregistered:**

```rust
// near/omni-token/src/lib.rs:141
self.token.internal_deposit(&account_id, amount.into());
// ↑ panics with "The account X is not registered" if account_id has no storage deposit
```

**Step 4 — `burn` executes regardless:**

```rust
// near/omni-token/src/lib.rs:149-150
self.token.internal_withdraw(&env::predecessor_account_id(), amount.into());
// ↑ withdraws from bridge's balance of old_token — succeeds independently
```

In NEAR's promise model, `Promise::and` submits both sub-promises as independent action receipts. They execute in parallel with no transactional rollback. The joint promise is detached, so there is no callback to detect or recover from failure. The `ft_on_transfer` return value of `U128(0)` has already been committed, so the NEP-141 `ft_transfer_call` resolver will not refund the user.

`migrate_deployed_token` only registers the bridge itself on the new token:

```rust
// near/omni-bridge/src/lib.rs:1651-1655
ext_token::ext(new_token.clone())
    .storage_deposit(&env::current_account_id(), Some(true))  // bridge only
    .detach();
```

No storage deposit is made for any user, so every user who has not independently registered on the new token before calling `swap_migrated_token` will trigger this failure path.

### Impact Explanation

**Critical — Permanent, irrecoverable loss of user funds.**

When `mint` fails:
- The user's old tokens have been transferred to the bridge (via `ft_transfer_call`) and `ft_on_transfer` returned 0, so the NEP-141 standard will not refund them.
- The `burn` receipt executes and destroys those old tokens from the bridge's balance.
- No new tokens are ever minted to the user.
- There is no recovery path: the old token supply is reduced, the new token supply is unchanged, and the user's value is gone.

This matches the allowed impact: **"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

### Likelihood Explanation

**Medium-High.** The `migrate_deployed_token` admin function does not register any users on the new token. Any user who calls `swap_migrated_token` without first calling `storage_deposit` on the new token contract will trigger the bug. Token migrations are infrequent but high-stakes events; users are unlikely to know they must pre-register on the new token before swapping, especially since the old token's `ft_transfer_call` interface gives no indication of this requirement. A single unaware user calling the swap is sufficient to trigger permanent loss.

### Recommendation

Replace the parallel, detached `burn.and(mint)` with a sequential, callback-guarded flow:

1. **Reverse the order**: mint first, then burn. If mint fails, burn is never submitted and the old tokens remain in the bridge's balance.
2. **Add a failure callback**: attach a `.then(Self::ext(...).swap_migrated_token_callback(sender_id, old_token, amount))` that refunds the old tokens to the user if mint fails (requires the bridge to hold them rather than burning immediately).
3. **Do not return `U128(0)` eagerly**: return a `PromiseOrValue::Promise(...)` from `ft_on_transfer` so the NEP-141 resolver can refund the full amount on failure.

A minimal safe pattern:

```rust
// mint first; only burn if mint succeeds
ext_token::ext(new_token)
    .mint(sender_id.clone(), amount, None)
    .then(
        Self::ext(env::current_account_id())
            .swap_migrated_token_callback(sender_id, old_token, amount)
    )
```

where `swap_migrated_token_callback` burns on success and re-credits (or refunds via a separate mechanism) on failure.

### Proof of Concept

1. DAO calls `migrate_deployed_token(Eth, "old.near", "new.near")`.
2. Alice holds 1000 `old.near` tokens. She has **not** called `storage_deposit` on `new.near`.
3. Alice calls `old.near::ft_transfer_call(bridge, 1000, {"SwapMigratedToken": null})`.
4. Bridge's `ft_on_transfer` returns `U128(0)` — Alice's 1000 old tokens are now held by the bridge.
5. Bridge submits two parallel receipts: `old.near::burn(1000)` and `new.near::mint(alice, 1000, None)`.
6. `new.near::mint` panics: `"The account alice is not registered"`.
7. `old.near::burn` succeeds: 1000 old tokens are destroyed from the bridge's balance.
8. Alice has 0 old tokens and 0 new tokens. The value is permanently lost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L275-279)
```rust
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
```

**File:** near/omni-bridge/src/lib.rs (L1651-1655)
```rust
        ext_token::ext(new_token.clone())
            .with_static_gas(STORAGE_DEPOSIT_GAS)
            .with_attached_deposit(NEP141_DEPOSIT)
            .storage_deposit(&env::current_account_id(), Some(true))
            .detach();
```

**File:** near/omni-bridge/src/lib.rs (L2738-2753)
```rust
    fn swap_migrated_token(
        &mut self,
        sender_id: AccountId,
        old_token: AccountId,
        amount: U128,
    ) -> Promise {
        let new_token = self
            .migrated_tokens
            .get(&old_token)
            .near_expect(BridgeError::TokenNotMigrated);

        let burn = ext_token::ext(old_token).burn(amount);
        let mint = ext_token::ext(new_token).mint(sender_id, amount, None);

        burn.and(mint)
    }
```

**File:** near/omni-token/src/lib.rs (L140-143)
```rust
        } else {
            self.token.internal_deposit(&account_id, amount.into());
            PromiseOrValue::Value(amount)
        }
```

**File:** near/omni-token/src/lib.rs (L146-151)
```rust
    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```
