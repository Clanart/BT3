### Title
Unchecked Parallel Burn-and-Mint in `swap_migrated_token` Causes Permanent Token Loss - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`swap_migrated_token` issues a `burn.and(mint)` joint promise with no callback to verify both sub-promises succeed. Because NEAR's `Promise::and` schedules both actions in parallel and neither is conditioned on the other's result, a failure in the `mint` leg (e.g., recipient lacks storage registration on the new token) permanently destroys the user's old tokens without issuing new ones.

---

### Finding Description

When a user sends old tokens to the bridge with `msg = "SwapMigratedToken"`, `ft_on_transfer` dispatches `swap_migrated_token` and immediately returns `U128(0)`, keeping all transferred tokens in the bridge's custody. [1](#0-0) 

Inside `swap_migrated_token`, the burn and mint are composed with `Promise::and`: [2](#0-1) 

In NEAR, `Promise::and` creates a joint promise where **both sub-promises execute unconditionally in parallel**. There is no callback attached to check whether both succeeded. The composed promise is then `.detach()`-ed at the call site, so its result is never inspected.

The `mint` function in `OmniToken` calls `internal_deposit` directly on the recipient account: [3](#0-2) 

`internal_deposit` panics if the recipient has not registered storage on the new token contract. When that panic occurs, the `mint` promise fails — but the `burn` promise has already executed (or executes regardless), permanently destroying the user's old tokens from the bridge's balance.

The `burn` function withdraws from `env::predecessor_account_id()` (the bridge contract): [4](#0-3) 

Because `ft_on_transfer` returned `U128(0)`, the old tokens are never refunded to the user. They are held by the bridge and then burned. If `mint` fails, the user has no recourse.

The symmetric failure also exists: if `burn` fails for any reason (e.g., gas exhaustion, token contract panic), `mint` still executes, issuing new tokens without destroying old ones — creating unbacked supply.

---

### Impact Explanation

**Primary impact (mint fails):** Permanent, irrecoverable loss of user funds. The user's old tokens are burned from the bridge's custody and no new tokens are issued. There is no retry mechanism, no refund path, and no callback to detect or revert the failure.

**Secondary impact (burn fails):** Unbacked supply inflation. New tokens are minted to the user while old tokens remain in circulation, breaking bridge collateralization for the migrated token pair.

Both match the allowed impact scope:
- *Permanent freezing / irrecoverable lock of user funds in bridge flows*
- *Balance / accounting corruption that breaks bridge collateralization*

---

### Likelihood Explanation

The primary failure path (mint fails due to missing storage registration) is realistic and reachable by any unprivileged user. NEAR's NEP-141 standard requires explicit `storage_deposit` before an account can hold a token. A user migrating from an old token to a new token has no automatic storage registration on the new contract. The `migrate_deployed_token` admin function only registers storage for the bridge contract itself, not for individual users: [5](#0-4) 

Any user who calls `SwapMigratedToken` without first registering storage on the new token triggers the loss. No privileged access is required; the attacker is the victim themselves, or a griefing party who can front-run the storage registration.

---

### Recommendation

Replace the unchecked `burn.and(mint)` with a sequential, callback-guarded pattern:

1. First call `burn` on the old token.
2. In the callback, verify the burn succeeded.
3. Only then call `mint` on the new token.
4. If `mint` fails in a subsequent callback, re-mint the old tokens back to the user (or store a claimable balance).

Additionally, require (or perform) a storage deposit on the new token for the recipient before executing the swap, mirroring the `check_or_pay_ft_storage` pattern used in `process_fin_transfer_to_near`: [6](#0-5) 

---

### Proof of Concept

1. DAO calls `migrate_deployed_token(Eth, old_token.near, new_token.near)`. Bridge registers storage for itself on `new_token.near` but not for users.
2. Alice holds 1000 `old_token.near` and has **not** called `storage_deposit` on `new_token.near`.
3. Alice calls `old_token.near::ft_transfer_call(bridge.near, 1000, "SwapMigratedToken")`.
4. Bridge's `ft_on_transfer` receives the call, invokes `swap_migrated_token(alice, old_token.near, 1000)`, and returns `U128(0)` — all 1000 tokens stay with the bridge.
5. `burn.and(mint)` is detached. Both promises execute in parallel:
   - `burn`: bridge calls `old_token.near::burn(1000)` → succeeds, 1000 old tokens destroyed.
   - `mint`: bridge calls `new_token.near::mint(alice, 1000, None)` → `internal_deposit(&alice, 1000)` panics because Alice has no storage registration → promise fails silently.
6. Alice has lost 1000 tokens permanently. No event, no refund, no retry. [7](#0-6)

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

**File:** near/omni-bridge/src/lib.rs (L1957-1977)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
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
