Let me examine the relevant production code to trace this exploit path.

Based on my examination of the production code, here is my analysis:

**Key code locations:**

`swap_migrated_token` at [1](#0-0) 

`burn` in omni-token at [2](#0-1) 

`mint` in omni-token at [3](#0-2) 

---

### Title
Non-Atomic Burn-and-Mint in `swap_migrated_token` Causes Permanent Token Loss — (`near/omni-bridge/src/lib.rs`)

### Summary

`swap_migrated_token` composes `burn` and `mint` with `Promise::and()`, which in NEAR schedules both as independent parallel receipts with no atomicity guarantee. If `burn` commits and `mint` panics (e.g., because the sender is not registered on the new token), the old tokens are permanently destroyed and no new tokens are ever minted. There is no callback or recovery path.

### Finding Description

The function `swap_migrated_token` constructs two cross-contract calls and joins them:

```rust
let burn = ext_token::ext(old_token).burn(amount);
let mint = ext_token::ext(new_token).mint(sender_id, amount, None);
burn.and(mint)
``` [4](#0-3) 

In NEAR Protocol, `Promise::and()` schedules both sub-promises as **separate, independent receipts**. They execute in parallel; neither is a prerequisite for the other, and a failure in one does **not** roll back the other.

The `burn` function calls `internal_withdraw` on the bridge's balance in the old token:

```rust
fn burn(&mut self, amount: U128) {
    self.assert_controller();
    self.token
        .internal_withdraw(&env::predecessor_account_id(), amount.into());
}
``` [2](#0-1) 

The `mint` function calls `internal_deposit` on the new token for the sender:

```rust
self.token.internal_deposit(&account_id, amount.into());
``` [5](#0-4) 

The NEAR FT standard's `internal_deposit` **panics** if `account_id` is not registered (has no storage). If the sender has not called `storage_deposit` on the new token contract, `mint` panics and its receipt is reverted — but `burn`'s receipt has already committed.

There is no `.then(callback)` on `burn.and(mint)` to detect this failure and restore state. [4](#0-3) 

The FT standard's `ft_resolve_transfer` will attempt to refund the user from the bridge's balance in the old token, but that balance is now 0 (burned), so the refund silently returns 0. The user's tokens are permanently lost.

### Impact Explanation

- Old token total supply decreases by `amount` (burn committed).
- New token total supply is unchanged (mint reverted).
- User receives 0 refund (bridge balance in old token is 0).
- Funds are permanently and irrecoverably destroyed.

This matches: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user funds.**

### Likelihood Explanation

The trigger condition — sender not registered on the new token — is realistic and common. Token migration is a user-facing flow. A user who holds old tokens and calls `ft_transfer_call` with `SwapMigratedToken` without having previously called `storage_deposit` on the new token contract will lose their entire transferred amount. No privileged access is required; any unprivileged user can trigger this against themselves, and there is no on-chain guard that checks registration before scheduling the burn.

### Recommendation

Replace the non-atomic `burn.and(mint)` with a sequential, callback-guarded pattern:

1. Call `mint` first.
2. In the success callback, call `burn`.
3. If `mint` fails, do not call `burn` and return the full amount to the FT standard for refund.

Alternatively, add a pre-check that verifies the sender is registered on the new token (via `storage_balance_of`) before scheduling either call, and abort with a full refund if not registered.

### Proof of Concept

```rust
// Unit test sketch:
// 1. Deploy old_token and new_token (bridge as controller).
// 2. Register user on old_token, do NOT register user on new_token.
// 3. Mint 1000 old tokens to user.
// 4. User calls old_token::ft_transfer_call(bridge, 1000, "SwapMigratedToken").
// 5. Bridge calls swap_migrated_token → burn.and(mint).
// 6. burn receipt: old_token total_supply -= 1000. Committed.
// 7. mint receipt: internal_deposit panics (user not registered). Reverted.
// 8. Assert: old_token::ft_total_supply() == 0 (tokens gone).
// 9. Assert: new_token::ft_total_supply() == 0 (tokens never minted).
// 10. Assert: user's balance on both tokens == 0.
```

### Citations

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

**File:** near/omni-token/src/lib.rs (L127-143)
```rust
    fn mint(
        &mut self,
        account_id: AccountId,
        amount: U128,
        msg: Option<String>,
    ) -> PromiseOrValue<U128> {
        self.assert_controller();

        if let Some(msg) = msg {
            self.token
                .internal_deposit(&env::predecessor_account_id(), amount.into());

            self.ft_transfer_call(account_id, amount, None, msg)
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
