### Title
Non-Atomic Burn-and-Mint in `swap_migrated_token` Causes Irrecoverable Fund Loss or Unbacked Token Minting — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`swap_migrated_token` issues a `burn.and(mint)` promise pair and immediately `.detach()`es it, returning `U128(0)` to the calling token contract. Because the result is never checked in a callback, if `burn` succeeds but `mint` fails the user's old tokens are permanently destroyed with no new tokens issued; if `burn` fails but `mint` succeeds, unbacked new tokens are created.

---

### Finding Description

In `ft_on_transfer`, when the message is `SwapMigratedToken`, the bridge calls `swap_migrated_token` and **detaches** the returned promise, immediately returning `U128(0)` (consume all tokens, no refund):

```rust
BridgeOnTransferMsg::SwapMigratedToken => {
    self.swap_migrated_token(sender_id, token_id, amount)
        .detach();
    PromiseOrPromiseIndexOrValue::Value(U128(0))
}
``` [1](#0-0) 

Inside `swap_migrated_token`, the two cross-contract calls are composed with `.and()` — parallel execution with no result-checking callback:

```rust
fn swap_migrated_token(...) -> Promise {
    let new_token = self.migrated_tokens.get(&old_token)...;
    let burn = ext_token::ext(old_token).burn(amount);
    let mint = ext_token::ext(new_token).mint(sender_id, amount, None);
    burn.and(mint)
}
``` [2](#0-1) 

Because the promise is `.detach()`ed, the NEP-141 token contract has already been told "0 tokens to refund" before either cross-contract call executes. The outcomes are:

| burn result | mint result | effect |
|---|---|---|
| success | success | correct |
| success | **failure** | old tokens permanently destroyed, user receives nothing |
| **failure** | success | old tokens remain in bridge (orphaned), unbacked new tokens minted to user |
| failure | failure | old tokens orphaned in bridge, user receives nothing |

The `mint` call can realistically fail if the recipient has no storage deposit on the new token contract, if the new token is paused, or if the bridge is not registered as a controller of the new token. The `burn` call can fail if the bridge is not a controller of the old token.

The analog to the external report is direct: just as burning SY tokens before atomically redeeming base assets creates a window where accounting can desync, here burning old tokens and minting new tokens in a detached, non-atomic parallel promise pair creates the same desync — the failure of one leg does not prevent or revert the other.

---

### Impact Explanation

**Scenario A (burn succeeds, mint fails):** The user's old tokens are burned from the bridge's balance. The NEP-141 token contract already received `U128(0)` and will not refund. The user permanently loses their tokens with no recourse. This is an irrecoverable lock of user funds.

**Scenario B (burn fails, mint succeeds):** The old tokens remain in the bridge with no mechanism to recover them (the user's `ft_transfer_call` already completed with 0 refund). The new tokens are minted to the user without the corresponding old tokens being destroyed. This creates unbacked supply of the new bridged token, breaking bridge collateralization.

Both outcomes fall within the allowed impact scope: permanent freezing of user funds (Scenario A) and unauthorized mint / accounting corruption (Scenario B).

---

### Likelihood Explanation

The `mint` path calls `OmniToken::mint` which calls `internal_deposit`, requiring the recipient to have a storage deposit on the new token contract. [3](#0-2) 

During a token migration, users of the old token are not guaranteed to have pre-registered storage on the new token. Any such user who calls `ft_transfer_call` with `SwapMigratedToken` will trigger Scenario A. This is a realistic, user-reachable condition requiring no privileged access.

---

### Recommendation

Replace the detached parallel promise with a sequential, callback-verified flow:

1. Call `burn` on the old token.
2. In the burn callback, verify success; only then call `mint` on the new token.
3. In the mint callback, verify success; if mint fails, re-mint the old tokens back to the user (or store a claimable record).

Alternatively, use `burn.then(mint_callback)` where the callback checks the burn result before issuing the mint, and ensure the `ft_on_transfer` return value is tied to the final outcome rather than immediately returning `U128(0)`.

---

### Proof of Concept

1. DAO registers `old_token → new_token` in `migrated_tokens`.
2. User holds 1000 `old_token` and has no storage deposit on `new_token`.
3. User calls `old_token.ft_transfer_call(bridge, 1000, "SwapMigratedToken")`.
4. Bridge's `ft_on_transfer` fires: `swap_migrated_token` is detached, `U128(0)` returned immediately — old tokens are consumed.
5. `burn.and(mint)` executes asynchronously:
   - `burn(1000)` on `old_token` succeeds (bridge is controller, has balance).
   - `mint(user, 1000)` on `new_token` **panics** because user has no storage deposit on `new_token`.
6. Result: 1000 `old_token` permanently destroyed, user receives 0 `new_token`. Funds are irrecoverably lost. [2](#0-1) [1](#0-0) [4](#0-3)

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
