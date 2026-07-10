The core code is clear. Let me verify the NEAR `Promise::and()` semantics and check whether there's any callback or recovery path after `burn.and(mint)`.

### Title
Non-Atomic `burn.and(mint)` in `swap_migrated_token` Permanently Destroys User Tokens on Mint Failure — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`swap_migrated_token` issues `burn.and(mint)` as two independent NEAR cross-contract receipts with no failure callback. Because NEAR's promise combinator does not roll back an already-executed receipt if a sibling receipt fails, a `mint` failure (e.g. recipient not registered on the new token) permanently destroys the user's old tokens with no recovery path.

---

### Finding Description

`migrate_deployed_token` (DAO-only) registers the mapping `old_token → new_token` and emits `MigrateTokenEvent`. After that, any holder of `old_token` can call:

```
old_token.ft_transfer_call(bridge, amount, msg="SwapMigratedToken")
```

The bridge's `ft_on_transfer` handler matches `BridgeOnTransferMsg::SwapMigratedToken`, immediately returns `U128(0)` (accepting all tokens), and **detaches** the swap promise: [1](#0-0) 

`swap_migrated_token` then constructs: [2](#0-1) 

`burn.and(mint)` schedules two independent action receipts. In NEAR's execution model, each receipt is a separate atomic unit; the runtime does **not** roll back a completed receipt if a sibling receipt fails. There is no `.then(callback)` to detect or compensate for a `mint` failure.

`OmniToken::mint` calls `internal_deposit(&account_id, ...)`: [3](#0-2) 

`internal_deposit` is the standard NEP-141 implementation, which **panics** if `account_id` has no storage registration on the new token. `migrate_deployed_token` only registers the bridge contract itself on the new token: [4](#0-3) 

Individual users are never auto-registered. If a user calls `swap_migrated_token` before registering on the new token, `burn` succeeds (old tokens destroyed), `mint` panics (new tokens never created), and the combined promise failure is silently discarded because the promise was `.detach()`ed.

---

### Impact Explanation

Permanent, irrecoverable destruction of user tokens. Old tokens are burned from the bridge's balance; new tokens are never minted. The `migrated_tokens` mapping still maps `old_token → new_token`, so the user cannot retry with the old token (they no longer hold any). There is no admin recovery function. This matches **Critical: Permanent freezing / irrecoverable loss of user funds in bridge token flows**.

---

### Likelihood Explanation

**Moderate-to-High.** The trigger condition — calling `swap_migrated_token` without prior storage registration on the new token — is the default state for any user who has not explicitly called `storage_deposit` on the new token contract. The migration UX provides no on-chain guard or warning. Any user who attempts the swap immediately after a migration announcement (before registering) loses their tokens permanently. No privileged access is required; the only precondition is that DAO has called `migrate_deployed_token`, which is the intended production flow.

---

### Recommendation

Replace the fire-and-forget `burn.and(mint)` with a sequenced, callback-guarded pattern:

1. Call `burn` first.
2. In a `.then(callback)` on the bridge, check the burn result.
3. Only if burn succeeded, call `mint`.
4. In a second `.then(callback)`, check the mint result; if it failed, re-mint the old tokens back to the user (or store a claimable balance).

Alternatively, invert the order: mint new tokens first, and only burn old tokens in the success callback of mint. This ensures old tokens are never destroyed unless new tokens are confirmed minted.

Additionally, add a guard in `swap_migrated_token` (or in `ft_on_transfer`) that verifies the user has storage registered on the new token before proceeding, reverting the entire `ft_transfer_call` (by returning the full `amount` instead of `0`) if not.

---

### Proof of Concept

1. DAO calls `migrate_deployed_token(Eth, old_token, new_token)`.
2. Alice holds 1000 `old_token`. She has **not** called `new_token.storage_deposit(alice)`.
3. Alice calls `old_token.ft_transfer_call(bridge, 1000, "\"SwapMigratedToken\"")`.
4. Bridge `ft_on_transfer` returns `U128(0)` — bridge now holds 1000 `old_token`. The `burn.and(mint)` promise is detached.
5. `burn` receipt executes on `old_token`: bridge's balance decreases by 1000. `old_token` total supply decreases by 1000.
6. `mint` receipt executes on `new_token`: `internal_deposit(&alice, 1000)` panics — Alice is not registered. Receipt fails silently (detached).
7. Assert: `old_token.ft_total_supply()` decreased by 1000; `new_token.ft_balance_of(alice)` = 0. Alice's tokens are permanently destroyed.

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

**File:** near/omni-token/src/lib.rs (L140-142)
```rust
        } else {
            self.token.internal_deposit(&account_id, amount.into());
            PromiseOrValue::Value(amount)
```
