### Title
Non-Atomic `burn.and(mint)` in `swap_migrated_token` Causes Permanent Irrecoverable Loss of User Funds - (File: near/omni-bridge/src/lib.rs)

### Summary

`swap_migrated_token` issues a parallel `burn.and(mint)` promise pair with no failure-handling callback. Because `ft_on_transfer` already returns `U128(0)` (consuming all transferred tokens) before either sub-promise executes, a mint failure leaves the user's old tokens permanently destroyed with no new tokens issued and no recovery path. Any unprivileged user who holds old (migrated) tokens and is not yet registered in the new token contract can trigger this irrecoverable loss by calling `ft_transfer_call` with the `SwapMigratedToken` message.

### Finding Description

**Entry point** — `ft_on_transfer` in `near/omni-bridge/src/lib.rs` (line 275–279):

```rust
BridgeOnTransferMsg::SwapMigratedToken => {
    self.swap_migrated_token(sender_id, token_id, amount)
        .detach();
    PromiseOrPromiseIndexOrValue::Value(U128(0))   // ← all tokens consumed immediately
}
```

The function returns `U128(0)` synchronously, before either the burn or the mint has executed. Under NEP-141, this tells the old token contract to keep all transferred tokens in the bridge's balance — there is no refund path.

**Vulnerable function** — `swap_migrated_token` in `near/omni-bridge/src/lib.rs` (lines 2738–2753):

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

    burn.and(mint)   // ← parallel, no callback, no atomicity
}
```

`burn.and(mint)` schedules both cross-contract calls in parallel. In NEAR's promise model, `.and()` does not provide atomicity: each sub-promise executes independently regardless of whether the other succeeds or fails. There is no `.then()` callback attached to inspect results or revert state.

**Mint failure path** — `OmniToken::mint` in `near/omni-token/src/lib.rs` (lines 127–143):

```rust
fn mint(&mut self, account_id: AccountId, amount: U128, msg: Option<String>) -> PromiseOrValue<U128> {
    self.assert_controller();
    // msg is None in swap_migrated_token
    self.token.internal_deposit(&account_id, amount.into());  // panics if account not registered
    PromiseOrValue::Value(amount)
}
```

`FungibleToken::internal_deposit` panics (causing the mint promise to fail) if `account_id` has no storage registration in the new token contract. This is standard NEP-141 behavior.

**Burn success path** — `OmniToken::burn` in `near/omni-token/src/lib.rs` (lines 146–151):

```rust
fn burn(&mut self, amount: U128) {
    self.assert_controller();
    self.token.internal_withdraw(&env::predecessor_account_id(), amount.into());
}
```

`predecessor_account_id()` here is the bridge contract, which holds the old tokens (received via `ft_transfer_call`). The burn succeeds independently of the mint.

**Exploit sequence:**
1. DAO calls `migrate_deployed_token(origin_chain, old_token, new_token)` — legitimate admin action, registers the migration.
2. User holds `amount` of `old_token` but has never registered storage in `new_token`.
3. User calls `old_token.ft_transfer_call(bridge, amount, SwapMigratedToken)`.
4. Bridge's `ft_on_transfer` returns `U128(0)` — bridge now holds `amount` of `old_token`.
5. `burn` executes on `old_token`: bridge's balance is withdrawn and destroyed. **Succeeds.**
6. `mint` executes on `new_token`: `internal_deposit` panics because user is not registered. **Fails.**
7. No callback exists to detect the failure or restore state.
8. User's `amount` of `old_token` is permanently destroyed; no `new_token` is issued.

### Impact Explanation

The user's bridged assets are irrecoverably destroyed. There is no admin function, no recovery path, and no refund mechanism — the bridge contract does not retain the old tokens (they are burned) and the new tokens are never minted. This is a permanent, irrecoverable lock of user funds in the bridge token migration flow, matching the Critical impact class: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

The trigger condition — a user not being registered in the new token contract — is realistic and common:
- Token migrations introduce a new contract address that users have never interacted with.
- NEP-141 storage registration is a separate, explicit step that many users omit.
- No on-chain check or warning prevents the user from calling `SwapMigratedToken` before registering.
- The `swap_migrated_token` function is publicly reachable by any holder of the old token via the standard `ft_transfer_call` interface.

### Recommendation

Replace the fire-and-forget `burn.and(mint)` with a sequential, callback-guarded pattern:

1. Call `burn` first.
2. In the burn callback, verify success; only then call `mint`.
3. In the mint callback, verify success; if it fails, re-mint the old tokens back to the user (or store a claimable balance).

Alternatively, check storage registration in the new token before burning, and revert the entire operation (returning `amount` from `ft_on_transfer`) if the user is not registered.

### Proof of Concept

```
1. DAO: migrate_deployed_token(Eth, "eth-usdc.bridge.near", "new-usdc.bridge.near")
2. Alice holds 1000 eth-usdc.bridge.near tokens.
3. Alice has never called storage_deposit on new-usdc.bridge.near.
4. Alice calls:
     eth-usdc.bridge.near.ft_transfer_call(
         receiver_id = "omni-bridge.near",
         amount = 1000,
         msg = "SwapMigratedToken"
     )
5. omni-bridge.near.ft_on_transfer returns U128(0) → tokens consumed.
6. burn(1000) on eth-usdc.bridge.near → succeeds (bridge held 1000).
7. mint(alice, 1000, None) on new-usdc.bridge.near → panics (alice not registered).
8. No callback. Alice's 1000 tokens are permanently destroyed.
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** near/omni-token/src/lib.rs (L127-151)
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
    }

    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```
