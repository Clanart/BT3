### Title
Silent Failure on Bridge Token Burn During `init_transfer` Allows Double-Spend of Bridged Assets — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

The `burn_tokens_if_needed` helper in the NEAR bridge contract fires the cross-contract burn call with `.detach()`, meaning its result is never checked. If the burn fails, the `InitTransferEvent` is still emitted and the transfer message is committed to state, allowing MPC signers to authorize a destination-chain mint while the NEAR-side bridge tokens remain unburned.

---

### Finding Description

In `near/omni-bridge/src/lib.rs`, `init_transfer_internal` is the core function that processes outbound transfers of NEAR-deployed bridge tokens. It calls `burn_tokens_if_needed`, which schedules the token burn as a detached promise:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();   // ← result is never observed
    }
}
``` [1](#0-0) 

After calling `burn_tokens_if_needed`, `init_transfer_internal` unconditionally emits `InitTransferEvent` and returns `U128(0)` (success):

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(...);
// ...
env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
U128(0)
``` [2](#0-1) 

In NEAR's execution model, `.detach()` schedules the burn as a separate receipt whose outcome is never awaited or checked by the calling contract. The state mutations in `init_transfer_internal` (storing the transfer message, emitting the event) are committed to the NEAR state trie regardless of whether the detached burn receipt succeeds or fails. There is no callback registered for `burn_tokens_if_needed`, and the function signature `&self` (not `&mut self`) confirms it cannot update contract state based on the burn outcome.

---

### Impact Explanation

**Critical — Direct unauthorized mint of bridged assets / unbacked supply.**

If the burn fails silently:
1. The `InitTransferEvent` is emitted with the full transfer amount.
2. MPC signers observe the event and produce a valid signature authorizing a mint on the destination chain (EVM, Solana, StarkNet).
3. The user claims freshly minted tokens on the destination chain.
4. The NEAR-side bridge tokens were **never burned** — the user still holds them.
5. The user can repeat the cycle indefinitely, minting unbacked tokens on the destination chain while retaining the originals on NEAR.

This directly breaks bridge collateralization and enables theft of value from the destination-chain token supply.

---

### Likelihood Explanation

**Medium-High.** The burn can fail for several realistic reasons reachable by an unprivileged user:

- **Insufficient gas**: `BURN_TOKEN_GAS` is a static constant. If the token contract's `burn` implementation consumes more gas than allocated (e.g., due to storage operations or complex logic), the receipt fails silently.
- **Token contract panic**: Any panic in the token's `burn` function (e.g., balance underflow, access control check) causes the receipt to fail without reverting the parent transaction.
- **Reentrancy-style scheduling**: A malicious or buggy token contract registered as a bridge token could be crafted to reliably fail its `burn` while appearing legitimate.

No privileged access is required. Any user holding bridge tokens can call `init_transfer` (or `ft_on_transfer` which routes to `init_transfer_internal`).

---

### Recommendation

Replace the fire-and-forget `.detach()` pattern with a chained callback that verifies the burn succeeded before committing the transfer:

```rust
ext_token::ext(token)
    .with_static_gas(BURN_TOKEN_GAS)
    .burn(amount)
    .then(
        Self::ext(env::current_account_id())
            .with_static_gas(BURN_CALLBACK_GAS)
            .burn_callback(transfer_message)
    )
```

The `burn_callback` should check `env::promise_result(0)` and only emit `InitTransferEvent` and store the transfer message if the burn succeeded. If the burn failed, it must revert or refund. This mirrors the fix applied by Moonwell Finance: validate the return/result of the critical external call before proceeding.

---

### Proof of Concept

**Step 1.** Attacker holds 1,000 units of a NEAR-deployed bridge token (e.g., `wbtc.bridge.near`).

**Step 2.** Attacker calls `ft_transfer_call` on the token contract, routing to the bridge's `ft_on_transfer`, which calls `init_transfer_internal`. The bridge token is a deployed token, so `burn_tokens_if_needed` is invoked.

**Step 3.** The burn cross-contract call is dispatched with `.detach()`. Due to insufficient `BURN_TOKEN_GAS` or a panic in the token's `burn`, the burn receipt fails. The parent receipt (containing `init_transfer_internal`) has already committed: the transfer message is stored and `InitTransferEvent` is emitted.

**Step 4.** MPC signers observe the `InitTransferEvent` on-chain and produce a valid MPC signature authorizing a mint of 1,000 units on the EVM destination chain.

**Step 5.** Attacker (or any relayer) submits the signed payload to `OmniBridge.finTransfer` on EVM. 1,000 bridged tokens are minted to the attacker's EVM address.

**Step 6.** Attacker's NEAR balance is unchanged (burn failed). Attacker repeats from Step 2, minting unbacked tokens on EVM indefinitely. [1](#0-0) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1806-1812)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
```

**File:** near/omni-bridge/src/lib.rs (L1829-1864)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
```
