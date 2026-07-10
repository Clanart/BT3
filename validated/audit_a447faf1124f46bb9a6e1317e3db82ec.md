### Title
Parallel Burn-and-Mint in `swap_migrated_token` Causes Permanent Token Loss if Mint Fails - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`swap_migrated_token` schedules a burn of the old token and a mint of the new token as two **parallel** NEAR promises using `burn.and(mint)`. Because the `ft_on_transfer` entry point immediately returns `U128(0)` (consuming all transferred tokens) and the promise is `.detach()`ed, if the mint call fails the burn still executes and the user permanently loses their tokens with no recovery path.

---

### Finding Description

When a user calls `ft_transfer_call` on an old (migrated) token with the `SwapMigratedToken` message, the bridge's `ft_on_transfer` dispatches `swap_migrated_token` and immediately returns `U128(0)`:

```rust
BridgeOnTransferMsg::SwapMigratedToken => {
    self.swap_migrated_token(sender_id, token_id, amount)
        .detach();
    PromiseOrPromiseIndexOrValue::Value(U128(0))  // tokens consumed immediately
}
``` [1](#0-0) 

Inside `swap_migrated_token`, the burn and mint are scheduled as **parallel** promises via `Promise::and()`:

```rust
fn swap_migrated_token(...) -> Promise {
    let new_token = self.migrated_tokens.get(&old_token)...;
    let burn = ext_token::ext(old_token).burn(amount);
    let mint = ext_token::ext(new_token).mint(sender_id, amount, None);
    burn.and(mint)   // parallel, not sequential
}
``` [2](#0-1) 

In NEAR's execution model, `Promise::and()` schedules both sub-promises independently. If the `mint` call panics (e.g., because the recipient has no storage registered on the new token contract), the `burn` receipt has already been dispatched and executes regardless. There is no callback to detect the failure and re-credit the user.

The `mint` function in `near/omni-token/src/lib.rs` calls `internal_deposit`, which panics if the recipient account has no storage registered:

```rust
fn mint(&mut self, account_id: AccountId, amount: U128, msg: Option<String>) -> PromiseOrValue<U128> {
    self.assert_controller();
    // ...
    self.token.internal_deposit(&account_id, amount.into()); // panics if no storage
    PromiseOrValue::Value(amount)
}
``` [3](#0-2) 

Because the promise is `.detach()`ed and `ft_on_transfer` already returned `U128(0)`, the NEP-141 `ft_transfer_call` mechanism has already consumed the user's old tokens. There is no mechanism to refund them.

---

### Impact Explanation

A user who calls `ft_transfer_call` with `SwapMigratedToken` but has not registered storage on the new token contract will:

1. Have their old tokens permanently burned (transferred to bridge, then burned via `ext_token::ext(old_token).burn(amount)`).
2. Receive zero new tokens (mint panics due to missing storage).
3. Have no recovery path — the transfer is not stored in `pending_transfers`, there is no cancel/refund function, and the old tokens are destroyed.

This is permanent, irrecoverable loss of user funds in a bridge token flow.

---

### Likelihood Explanation

The scenario is realistic and reachable by any unprivileged user:

- Token migrations are a normal protocol operation (`migrate_deployed_token` is a DAO function that can be called to migrate tokens).
- A user holding old tokens who attempts to swap them without first registering storage on the new token contract will trigger this path.
- No privileged access is required — the attacker-controlled entry point is `ft_transfer_call` on the old token contract, callable by any token holder.
- The user may not know they need to register storage on the new token before swapping.

---

### Recommendation

Replace the parallel `burn.and(mint)` pattern with a sequential, callback-guarded pattern: first mint the new tokens, and only burn the old tokens in the success callback. If the mint fails, return the old tokens to the user (return non-zero from `ft_on_transfer`).

```rust
fn swap_migrated_token(...) -> Promise {
    let new_token = self.migrated_tokens.get(&old_token)...;
    // Mint first, burn only on success in callback
    ext_token::ext(new_token)
        .mint(sender_id.clone(), amount, None)
        .then(
            Self::ext(env::current_account_id())
                .swap_migrated_token_callback(sender_id, old_token, amount)
        )
}
```

In `swap_migrated_token_callback`, burn the old tokens only if the mint succeeded; otherwise, do not return `U128(0)` from `ft_on_transfer` so the old tokens are refunded.

---

### Proof of Concept

1. DAO calls `migrate_deployed_token` to migrate `old.token` → `new.token`.
2. Alice holds 1000 `old.token` but has **not** registered storage on `new.token`.
3. Alice calls `ft_transfer_call` on `old.token` with `receiver_id = bridge`, `amount = 1000`, `msg = "SwapMigratedToken"`.
4. Bridge's `ft_on_transfer` calls `swap_migrated_token(alice, old.token, 1000).detach()` and returns `U128(0)`.
5. NEP-141 sees `U128(0)` returned → does not refund Alice → Alice's 1000 `old.token` are now held by the bridge.
6. `burn.and(mint)` executes in parallel:
   - `burn(1000)` on `old.token` succeeds → bridge's balance of `old.token` is destroyed.
   - `mint(alice, 1000, None)` on `new.token` panics → `internal_deposit` fails because Alice has no storage on `new.token`.
7. Alice has 0 `old.token` (burned) and 0 `new.token` (mint failed). Tokens are permanently lost. [4](#0-3) [1](#0-0)

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

**File:** near/omni-token/src/lib.rs (L124-151)
```rust
#[near]
impl MintAndBurn for OmniToken {
    #[payable]
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
    }

    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```
