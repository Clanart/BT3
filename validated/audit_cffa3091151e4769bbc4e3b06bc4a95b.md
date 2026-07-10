### Title
Parallel `burn.and(mint)` in `swap_migrated_token` Enables Unbacked Token Minting or Permanent Token Loss - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`swap_migrated_token` composes the old-token burn and new-token mint as a **parallel** NEAR joint promise (`burn.and(mint)`) and immediately `.detach()`es it with no callback. In NEAR's execution model, `Promise::and` schedules both sub-promises as independent action receipts that execute regardless of each other's outcome. If the burn receipt fails the mint receipt still executes (unbacked new-token supply), and if the mint receipt fails the burn receipt still executes (permanent destruction of the user's tokens). The correct pattern is sequential: burn first, then mint only inside a callback that confirms the burn succeeded.

---

### Finding Description

`ft_on_transfer` handles the `SwapMigratedToken` message variant by calling `swap_migrated_token` and immediately detaching the returned promise:

```rust
BridgeOnTransferMsg::SwapMigratedToken => {
    self.swap_migrated_token(sender_id, token_id, amount)
        .detach();
    PromiseOrPromiseIndexOrValue::Value(U128(0))
}
```

`swap_migrated_token` itself is:

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

`Promise::and` in the NEAR SDK creates a **joint promise** whose two sub-promises are dispatched as separate, independent action receipts. Each receipt executes in its own execution context; a failure in one does **not** roll back or prevent the other. Because the joint promise is `.detach()`ed, there is no callback that could observe the individual outcomes and compensate.

The `burn` call invokes `OmniToken::burn`, which calls `internal_withdraw` from `env::predecessor_account_id()` (the bridge contract, which holds the old tokens after `ft_transfer_call`). The `mint` call invokes `OmniToken::mint`, which calls `internal_deposit` to `sender_id` on the new token contract.

Two failure modes exist:

**Mode A – Unbacked mint (burn fails, mint succeeds):** If the old-token receipt fails for any reason (old token paused, contract upgraded, gas exhaustion in that receipt), the new-token mint receipt still executes. The user receives new tokens without the old tokens being destroyed, inflating the new token's supply beyond its backing.

**Mode B – Permanent token loss (mint fails, burn succeeds):** If the new-token receipt fails (new token paused, bridge lacks minting rights, gas exhaustion), the old-token burn receipt still executes. The user's old tokens are destroyed and no new tokens are minted; the funds are permanently lost.

---

### Impact Explanation

- **Mode A** maps to: *Critical – Direct theft / unauthorized mint of bridged assets.* New tokens are minted without a corresponding burn of old tokens, breaking the 1:1 migration invariant and inflating supply unbacked by any locked or burned collateral.
- **Mode B** maps to: *Critical – Permanent freezing / irrecoverable lock of user funds.* The user's old tokens are burned and no new tokens are issued; the value is permanently destroyed with no recovery path.

---

### Likelihood Explanation

The entry point is fully unprivileged: any token holder of a migrated token can call `ft_transfer_call` on the old token with `msg = "SwapMigratedToken"` to trigger this path. No special role or key is required.

**Mode B** (permanent loss) is the more immediately realistic scenario. If the new token contract is paused at the moment of migration (e.g., during a deployment or upgrade window), or if the bridge's controller role on the new token has not yet been granted, the mint receipt will fail while the burn receipt succeeds. A user who migrates during such a window loses their tokens permanently.

**Mode A** (unbacked mint) requires the old token's burn to fail while the new token's mint succeeds. This is less common in steady state but becomes realistic if the old token contract is paused or upgraded after `migrate_deployed_token` is called but before all users have migrated.

---

### Recommendation

Replace the parallel `burn.and(mint)` with a sequential pattern: burn first, then mint inside a `#[private]` callback that checks the burn result and only proceeds if it succeeded. If the burn failed, the callback should refund the user's old tokens (which were already transferred to the bridge via `ft_transfer_call`).

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

    ext_token::ext(old_token.clone())
        .burn(amount)
        .then(
            Self::ext(env::current_account_id())
                .swap_migrated_token_callback(sender_id, old_token, new_token, amount),
        )
}

#[private]
pub fn swap_migrated_token_callback(
    &mut self,
    sender_id: AccountId,
    old_token: AccountId,
    new_token: AccountId,
    amount: U128,
    #[callback_result] burn_result: Result<(), PromiseError>,
) -> Promise {
    if burn_result.is_err() {
        // Burn failed: refund old tokens to sender
        return ext_token::ext(old_token)
            .with_attached_deposit(ONE_YOCTO)
            .ft_transfer(sender_id, amount, None);
    }
    ext_token::ext(new_token).mint(sender_id, amount, None)
}
```

---

### Proof of Concept

1. DAO calls `migrate_deployed_token(origin_chain, old_token, new_token)` — this registers the migration and pauses the old token's bridge operations.
2. Attacker (or any user) calls `old_token.ft_transfer_call(bridge, amount, "SwapMigratedToken")`.
3. Bridge's `ft_on_transfer` is invoked; it calls `swap_migrated_token(sender, old_token, amount).detach()` and returns `U128(0)` (no refund of old tokens).
4. NEAR runtime schedules two independent receipts: `old_token.burn(amount)` and `new_token.mint(sender, amount)`.
5. If `old_token` is paused at this moment, the burn receipt panics and is rolled back. The mint receipt executes independently and succeeds.
6. Result: `sender` receives `amount` of new tokens; `amount` of old tokens remain in the bridge contract (not burned). New token supply is inflated without backing. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L275-279)
```rust
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
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

**File:** near/omni-token/src/lib.rs (L146-151)
```rust
    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```
